"""Podman-based sandbox: every agent-authored program runs in a throwaway container."""
from __future__ import annotations

import asyncio
import json
import shutil
import uuid
from dataclasses import dataclass

from .config import Settings

MAX_OUT = 8000
MARK = "__BRAIN_RESULT__"


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
        s = f"exit={self.exit_code}" + (" TIMEOUT" if self.timed_out else "")
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
        self._build_lock = asyncio.Lock()
        self._running: set[str] = set()

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
            out, err = await asyncio.wait_for(
                proc.communicate(stdin.encode() if stdin is not None else None), timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return ExecResult(False, -1, "", "timeout", timed_out=True)
        except asyncio.CancelledError:
            proc.kill()
            raise
        return ExecResult(proc.returncode == 0, proc.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace"))

    async def status(self) -> dict:
        if not shutil.which(self.s.podman):
            return {"ok": False, "error": "podman binary not found", "image": False}
        r = await self._exec(self._base() + ["info", "--format", "{{.Host.Arch}}"], 20)
        if not r.ok:
            return {"ok": False, "error": (r.stderr or "podman unreachable").strip()[:200], "image": False}
        img = await self._exec(self._base() + ["image", "exists", self.s.sandbox_image], 20)
        self._image_ready = img.ok
        return {"ok": True, "image": img.ok, "running": len(self._running)}

    async def ensure_image(self) -> None:
        if self._image_ready:
            return
        async with self._build_lock:
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

    async def run(
        self,
        cmd: list[str],
        *,
        timeout: float | None = None,
        stdin: str | None = None,
        evolvable_rw: bool = False,
        mount_evolvable: bool = False,
    ) -> ExecResult:
        """Execute `cmd` in a fresh container with limited resources."""
        await self.ensure_image()
        timeout = timeout or self.s.sandbox_timeout
        name = f"brain-{uuid.uuid4().hex[:10]}"
        args = self._base() + [
            "run", "--rm", "-i" if stdin is not None else "--init", "--name", name, "--label", "brain=1",
            "--memory", self.s.sandbox_memory, "--cpus", self.s.sandbox_cpus, "--pids-limit", "256",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "-v", f"{self.s.workspace_dir}:/workspace",
            "-v", f"{self.s.sandbox_src}:/runner:ro",
            "-w", "/workspace",
        ]
        if mount_evolvable:
            args += ["-v", f"{self.s.evolvable_dir}:/evolvable{'' if evolvable_rw else ':ro'}"]
        args += [self.s.sandbox_image] + cmd
        self._running.add(name)
        try:
            res = await self._exec(args, timeout + 15, stdin)
            if res.timed_out:
                await self._exec(self._base() + ["kill", name], 15)
            return res
        finally:
            self._running.discard(name)

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

    async def pytest(self, rel_path: str, *, evolvable: bool = False) -> ExecResult:
        if evolvable:
            return await self.run(
                ["python", "-m", "pytest", "-x", "-q", "--no-header", "-p", "no:cacheprovider", rel_path],
                mount_evolvable=True,
            )
        return await self.run(
            ["env", "PYTHONPATH=/workspace/tools", "python", "-m", "pytest", "-x", "-q", "--no-header",
             "-p", "no:cacheprovider", rel_path]
        )

    async def kill_all(self) -> None:
        """Hard-stop every container started by Brain (kill switch)."""
        r = await self._exec(self._base() + ["ps", "-q", "--filter", "label=brain=1"], 15)
        ids = r.stdout.split()
        if ids:
            await self._exec(self._base() + ["kill", *ids], 30)
        self._running.clear()
