"""Podman sandbox: one long-lived, resource-limited container; every agent program runs via `podman exec`."""
from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
from dataclasses import dataclass

from .config import Settings

MAX_OUT = 8000
MARK = "__BRAIN_RESULT__"
KILLED_EXIT_CODES = (124, 137)  # `timeout` / SIGKILL


@dataclass
class ExecResult:
    ok: bool
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False

    def brief(self) -> str:
        out = self.stdout[-MAX_OUT:]
        err = self.stderr[-2000:]
        s = f"exit={self.exit_code}" + (" TIMEOUT/KILLED" if self.timed_out else "")
        if out:
            s += f"\n[stdout]\n{out}"
        if err:
            s += f"\n[stderr]\n{err}"
        return s


class SandboxError(RuntimeError):
    pass


class Sandbox:
    def __init__(self, settings: Settings):
        self.s = settings
        self._image_ready = False
        self._lock = asyncio.Lock()
        # One container per workspace, so parallel Brain instances never collide.
        self.name = "brain-sandbox-" + hashlib.sha1(str(settings.workspace_dir).encode()).hexdigest()[:8]
        self.active = 0

    def _base(self) -> list[str]:
        cmd = [self.s.podman]
        if self.s.podman_connection:
            cmd += ["--connection", self.s.podman_connection]
        return cmd

    async def _exec(self, args: list[str], timeout: float, stdin: str | None = None) -> ExecResult:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(stdin.encode() if stdin is not None else None), timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return ExecResult(False, -1, "", "timeout", timed_out=True)
        except asyncio.CancelledError:
            proc.kill()
            raise
        return ExecResult(proc.returncode == 0, proc.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace"))

    async def _container_running(self) -> bool:
        r = await self._exec(self._base() + ["inspect", "-f", "{{.State.Running}}", self.name], 20)
        return r.ok and r.stdout.strip() == "true"

    async def status(self) -> dict:
        if not shutil.which(self.s.podman):
            return {"ok": False, "error": "podman binary not found", "image": False}
        r = await self._exec(self._base() + ["info", "--format", "{{.Host.Arch}}"], 20)
        if not r.ok:
            return {"ok": False, "error": (r.stderr or "podman unreachable").strip()[:200], "image": False}
        img = await self._exec(self._base() + ["image", "exists", self.s.sandbox_image], 20)
        self._image_ready = img.ok
        return {"ok": True, "image": img.ok, "container": self.name, "running": await self._container_running(), "active": self.active}

    async def ensure_image(self) -> None:
        if self._image_ready:
            return
        async with self._lock:
            if (await self._exec(self._base() + ["image", "exists", self.s.sandbox_image], 20)).ok:
                self._image_ready = True
                return
            r = await self._exec(
                self._base() + ["build", "-t", self.s.sandbox_image, "-f", str(self.s.sandbox_src / "Containerfile"), str(self.s.sandbox_src)],
                900,
            )
            if not r.ok:
                raise SandboxError(f"image build failed: {r.stderr[-400:]}")
            self._image_ready = True

    async def ensure_container(self) -> None:
        """Start the persistent container (visible in `podman ps` / Podman Desktop) if it is not running."""
        await self.ensure_image()
        async with self._lock:
            if await self._container_running():
                return
            await self._exec(self._base() + ["rm", "-f", self.name], 30)
            r = await self._exec(
                self._base() + [
                    "run", "-d", "--init", "--name", self.name, "--label", "brain=1",
                    "--memory", self.s.sandbox_memory, "--cpus", self.s.sandbox_cpus, "--pids-limit", "512",
                    "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                    "-v", f"{self.s.workspace_dir}:/workspace",
                    "-v", f"{self.s.sandbox_src}:/runner:ro",
                    "-v", f"{self.s.evolvable_dir}:/evolvable:ro",
                    "-w", "/workspace", self.s.sandbox_image, "sleep", "infinity",
                ],
                90,
            )
            if not r.ok:
                raise SandboxError(f"cannot start sandbox container: {r.stderr[-400:]}")

    async def run(self, cmd: list[str], *, timeout: float | None = None, stdin: str | None = None) -> ExecResult:
        """Execute `cmd` inside the sandbox container, killing it after `timeout` seconds."""
        await self.ensure_container()
        timeout = int(timeout or self.s.sandbox_timeout)
        args = self._base() + ["exec"] + (["-i"] if stdin is not None else []) + [
            self.name, "timeout", "-s", "KILL", str(timeout), *cmd,
        ]
        self.active += 1
        try:
            res = await self._exec(args, timeout + 20, stdin)
        finally:
            self.active -= 1
        if res.exit_code in KILLED_EXIT_CODES:
            res.timed_out = True
        return res

    async def python(self, code: str, timeout: float | None = None) -> ExecResult:
        return await self.run(["python", "-c", code], timeout=timeout)

    async def shell(self, command: str, timeout: float | None = None) -> ExecResult:
        return await self.run(["sh", "-c", command], timeout=timeout)

    async def run_tool(self, name: str, args: dict) -> dict:
        res = await self.run(["python", "/runner/tool_runner.py", name], stdin=json.dumps(args))
        for line in reversed(res.stdout.splitlines()):
            if line.startswith(MARK):
                return json.loads(line[len(MARK):])
        return {"ok": False, "error": res.brief()}

    async def pytest(self, path: str, *, evolvable: bool = False) -> ExecResult:
        env = [] if evolvable else ["env", "PYTHONPATH=/workspace/tools"]
        return await self.run([*env, "python", "-m", "pytest", "-x", "-q", "--no-header", "-p", "no:cacheprovider", path])

    async def kill_all(self) -> None:
        """Hard-stop (kill switch): remove every container started by Brain; the next call recreates it."""
        r = await self._exec(self._base() + ["ps", "-aq", "--filter", "label=brain=1"], 15)
        ids = r.stdout.split()
        if ids:
            await self._exec(self._base() + ["rm", "-f", *ids], 40)

    async def close(self) -> None:
        await self._exec(self._base() + ["rm", "-f", self.name], 40)
