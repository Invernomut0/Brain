"""Podman sandbox: one long-lived, resource-limited container; every agent program runs via `podman exec`."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import shlex
import shutil
from dataclasses import dataclass

from .config import Settings

MAX_OUT = 8000
MARK = "__BRAIN_RESULT__"
KILLED_EXIT_CODES = (124, 137)  # `timeout` / SIGKILL

# Virtual environments live in the container (not in the workspace: no thousands of files to list, publish or snapshot);
# they vanish with the container, so a missing one is simply recreated.
VENV_ROOT = "/opt/venvs"
DEFAULT_VENV = "default"
VENV_RE = re.compile(r"^[a-z][a-z0-9_-]{0,23}$")
PACKAGE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-\[\],]*(\s?(==|>=|<=|~=|!=|<|>)\s?[A-Za-z0-9.*+!_\-]+(,\s?(==|>=|<=|~=|!=|<|>)\s?[A-Za-z0-9.*+!_\-]+)*)?$")
NO_VENV = "BRAIN_NO_VENV"
# Runs `cmd` with the venv first on PATH; pip refuses to touch the system site-packages.
_GUARD = (
    'v="$1"; shift; [ -x "$v/bin/python" ] || { echo BRAIN_NO_VENV >&2; exit 97; }; '
    'export VIRTUAL_ENV="$v" PATH="$v/bin:$PATH" PIP_REQUIRE_VIRTUALENV=1; exec "$@"'
)


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


class VenvError(SandboxError):
    """The virtual environment is missing or its name/packages are not valid."""


def venv_dir(name: str) -> str:
    if not VENV_RE.match(str(name)):
        raise VenvError("invalid venv name: 1-24 characters, lowercase letters, digits, '_' or '-', starting with a letter")
    return f"{VENV_ROOT}/{name}"


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

    async def in_venv(self, cmd: list[str], venv: str = DEFAULT_VENV, *, timeout: float | None = None, stdin: str | None = None) -> ExecResult:
        """Run `cmd` with the virtual environment activated; VenvError when it does not exist."""
        res = await self.run(["sh", "-c", _GUARD, "sh", venv_dir(venv), *cmd], timeout=timeout, stdin=stdin)
        if res.exit_code == 97 and NO_VENV in res.stderr:
            raise VenvError(f"virtual environment '{venv}' does not exist: call create_venv(name='{venv}') first")
        return res

    async def python_in_venv(self, code: str, venv: str = DEFAULT_VENV, timeout: float | None = None) -> ExecResult:
        return await self.in_venv(["python", "-c", code], venv, timeout=timeout)

    async def create_venv(self, name: str = DEFAULT_VENV, packages: list[str] | None = None, timeout: float = 600) -> ExecResult:
        """Create the venv if missing (it sees the image's preinstalled packages) and pip-install `packages` into it."""
        d = venv_dir(name)
        pkgs = [str(p).strip() for p in (packages or [])]
        bad = [p for p in pkgs if not PACKAGE_RE.match(p)]
        if bad:
            raise VenvError(f"invalid package spec {bad[0]!r}: use names with optional version pins such as 'pandas' or 'pandas>=2'")
        script = f'[ -x "{d}/bin/python" ] || python -m venv --system-site-packages "{d}" || exit 1; '
        if pkgs:
            script += f'"{d}/bin/python" -m pip install --no-cache-dir --disable-pip-version-check -q {" ".join(shlex.quote(p) for p in pkgs)} || exit 2; '
        script += f'"{d}/bin/python" -c "import sys; print(sys.version.split()[0])"'
        return await self.run(["sh", "-c", script], timeout=timeout)

    async def _prefer_venv(self, cmd: list[str], **kw) -> ExecResult:
        """Agent-made tools run in the default venv when it exists (so its packages are importable)."""
        try:
            return await self.in_venv(cmd, DEFAULT_VENV, **kw)
        except VenvError:
            return await self.run(cmd, **kw)

    async def shell(self, command: str, timeout: float | None = None) -> ExecResult:
        return await self.run(["sh", "-c", command], timeout=timeout)

    async def run_tool(self, name: str, args: dict) -> dict:
        res = await self._prefer_venv(["python", "/runner/tool_runner.py", name], stdin=json.dumps(args))
        for line in reversed(res.stdout.splitlines()):
            if line.startswith(MARK):
                return json.loads(line[len(MARK):])
        return {"ok": False, "error": res.brief()}

    async def pytest(self, path: str, *, evolvable: bool = False) -> ExecResult:
        env = [] if evolvable else ["env", "PYTHONPATH=/workspace/tools"]
        cmd = [*env, "python", "-m", "pytest", "-x", "-q", "--no-header", "-p", "no:cacheprovider", path]
        return await (self.run(cmd) if evolvable else self._prefer_venv(cmd))

    async def kill_all(self) -> None:
        """Hard-stop (kill switch): remove this instance's container; the next call recreates it (stop.sh removes them all)."""
        r = await self._exec(self._base() + ["ps", "-aq", "--filter", "label=brain=1", "--filter", f"name=^{self.name}$"], 15)
        ids = r.stdout.split()
        if ids:
            await self._exec(self._base() + ["rm", "-f", *ids], 40)

    async def close(self) -> None:
        await self._exec(self._base() + ["rm", "-f", self.name], 40)
