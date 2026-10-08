"""Controlled self-evolution: versioned prompts and sandbox-tested hook code, with git rollback.

Safety model: the core (backend/) is never writable by agents. They may only change
files under evolvable/, which is executed exclusively inside the Podman sandbox.
Every change is a git commit; failed tests or degraded performance trigger a revert.
"""
from __future__ import annotations

import asyncio
import json
import re
import time

from .bus import EventBus
from .config import Settings
from .db import Database
from .prompts import DEFAULTS, SEED_CONTEXT, SEED_PRIORITIZE, SEED_TEST
from .sandbox import MARK, Sandbox

NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,30}$")
GIT = ["git", "-c", "user.name=Brain", "-c", "user.email=brain@local"]


class Evolution:
    def __init__(self, settings: Settings, db: Database, bus: EventBus, sandbox: Sandbox):
        self.s, self.db, self.bus, self.sandbox = settings, db, bus, sandbox
        self.hook_failures: dict[str, int] = {}

    # ---- git ---------------------------------------------------------------
    async def _git(self, *args: str) -> tuple[int, str]:
        proc = await asyncio.create_subprocess_exec(
            *GIT, *args, cwd=self.s.repo_dir, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
        )
        out, _ = await proc.communicate()
        return proc.returncode or 0, out.decode(errors="replace").strip()

    async def _commit(self, message: str) -> str:
        await self._git("add", "-A", "evolvable")
        await self._git("commit", "-m", message, "--allow-empty", "--", "evolvable")
        _, sha = await self._git("rev-parse", "HEAD")
        return sha

    async def seed(self) -> None:
        """Create the evolvable zone with default prompts/hooks on first run."""
        if not (self.s.repo_dir / ".git").exists():
            await self._git("init", "-b", "main")
        ev = self.s.evolvable_dir
        files = {
            ev / "hooks" / "prioritize.py": SEED_PRIORITIZE,
            ev / "hooks" / "context.py": SEED_CONTEXT,
            ev / "tests" / "test_prioritize.py": SEED_TEST,
        }
        for role, text in DEFAULTS.items():
            files[ev / "prompts" / f"{role}.md"] = text
        created = False
        for path, text in files.items():
            if not path.exists():
                path.write_text(text)
                created = True
        if created:
            sha = await self._commit("evolve: seed defaults")
            for role in DEFAULTS:
                if not self.db.one("SELECT 1 FROM prompt_versions WHERE role=?", (role,)):
                    self.db.execute(
                        "INSERT INTO prompt_versions(role,version,sha,reason,created) VALUES(?,?,?,?,?)",
                        (role, 1, sha, "seed", time.time()),
                    )

    # ---- prompts -------------------------------------------------------------
    def prompt(self, role: str) -> str:
        p = self.s.evolvable_dir / "prompts" / f"{role}.md"
        if p.exists():
            return p.read_text()
        return DEFAULTS.get(role, DEFAULTS["executor"])

    def record_run(self, role: str, success: bool) -> None:
        self.db.execute(
            "UPDATE prompt_versions SET runs=runs+1, successes=successes+? WHERE role=? AND active=1",
            (1 if success else 0, role),
        )

    async def propose_prompt(self, role: str, text: str, reason: str) -> dict:
        if role not in DEFAULTS:
            return {"ok": False, "error": f"unknown role {role}"}
        if not (80 <= len(text) <= 4000):
            return {"ok": False, "error": "prompt length must be 80-4000 chars"}
        if role in ("planner", "critic", "reflector") and "JSON" not in text:
            return {"ok": False, "error": "prompt must keep the JSON output contract"}
        cur = self.db.one("SELECT * FROM prompt_versions WHERE role=? AND active=1", (role,))
        (self.s.evolvable_dir / "prompts" / f"{role}.md").write_text(text)
        sha = await self._commit(f"evolve: prompt {role}: {reason[:80]}")
        version = (cur["version"] + 1) if cur else 1
        self.db.execute("UPDATE prompt_versions SET active=0 WHERE role=?", (role,))
        self.db.execute(
            "INSERT INTO prompt_versions(role,version,sha,reason,created) VALUES(?,?,?,?,?)",
            (role, version, sha, reason, time.time()),
        )
        await self._log("prompt", role, sha, "applied", reason, f"v{version}")
        return {"ok": True, "version": version}

    async def check_rollbacks(self) -> list[str]:
        """Revert a prompt version whose success rate is clearly worse than its predecessor."""
        rolled = []
        for row in self.db.query("SELECT * FROM prompt_versions WHERE active=1 AND version>1 AND runs>=5"):
            prev = self.db.one(
                "SELECT * FROM prompt_versions WHERE role=? AND version=?", (row["role"], row["version"] - 1)
            )
            if not prev or prev["runs"] < 3:
                continue
            if row["successes"] / row["runs"] < prev["successes"] / prev["runs"] - 0.15:
                await self._git("checkout", prev["sha"], "--", f"evolvable/prompts/{row['role']}.md")
                sha = await self._commit(f"evolve: rollback prompt {row['role']} v{row['version']}")
                self.db.execute("UPDATE prompt_versions SET active=0 WHERE role=?", (row["role"],))
                self.db.execute("UPDATE prompt_versions SET active=1 WHERE id=?", (prev["id"],))
                await self._log("prompt", row["role"], sha, "rolled_back", "performance regression", f"v{row['version']}")
                rolled.append(row["role"])
        return rolled

    # ---- hook code ------------------------------------------------------------
    async def propose_hook(self, name: str, code: str, test_code: str, reason: str) -> dict:
        if not NAME_RE.match(name):
            return {"ok": False, "error": "invalid hook name"}
        if not test_code.strip():
            return {"ok": False, "error": "tests are mandatory"}
        hook = self.s.evolvable_dir / "hooks" / f"{name}.py"
        test = self.s.evolvable_dir / "tests" / f"test_{name}.py"
        old = {p: (p.read_text() if p.exists() else None) for p in (hook, test)}
        hook.write_text(code)
        test.write_text(test_code)
        res = await self.sandbox.pytest(f"/evolvable/tests/test_{name}.py", evolvable=True)
        if not res.ok:
            for p, txt in old.items():
                p.write_text(txt) if txt is not None else p.unlink(missing_ok=True)
            await self._log("hook", name, "", "rejected", reason, res.brief()[-800:])
            return {"ok": False, "error": "tests failed", "output": res.brief()[-800:]}
        sha = await self._commit(f"evolve: hook {name}: {reason[:80]}")
        await self._log("hook", name, sha, "applied", reason, "tests passed")
        self.hook_failures[name] = 0
        return {"ok": True, "sha": sha}

    async def rollback_hook(self, name: str, why: str) -> None:
        rel = f"evolvable/hooks/{name}.py"
        _, out = await self._git("log", "--format=%H", "-n", "2", "--", rel)
        shas = out.split()
        if len(shas) >= 2:
            await self._git("checkout", shas[1], "--", rel)
        else:
            await self._git("rm", "-f", "--ignore-unmatch", rel)
        sha = await self._commit(f"evolve: rollback hook {name}")
        await self._log("hook", name, sha, "rolled_back", why, "")
        self.hook_failures[name] = 0

    async def call_hook(self, name: str, func: str, *args):
        """Run an evolvable hook in the sandbox; None on any failure (caller uses the default)."""
        if not (self.s.evolvable_dir / "hooks" / f"{name}.py").exists():
            return None
        res = await self.sandbox.run(
            ["python", "/runner/hook_runner.py", name, func],
            stdin=json.dumps({"args": list(args)}, default=str), mount_evolvable=True, timeout=45,
        )
        for line in reversed(res.stdout.splitlines()):
            if line.startswith(MARK):
                out = json.loads(line[len(MARK):])
                if out["ok"]:
                    self.hook_failures[name] = 0
                    return out["result"]
                break
        self.hook_failures[name] = self.hook_failures.get(name, 0) + 1
        await self.bus.publish("evolution.hook_error", None, hook=name, failures=self.hook_failures[name])
        if self.hook_failures[name] >= 3:
            await self.rollback_hook(name, "3 consecutive runtime failures")
        return None

    async def _log(self, kind: str, target: str, sha: str, status: str, reason: str, detail: str) -> None:
        self.db.execute(
            "INSERT INTO evolutions(ts,kind,target,sha,status,reason,detail) VALUES(?,?,?,?,?,?,?)",
            (time.time(), kind, target, sha, status, reason[:300], detail[:1000]),
        )
        await self.bus.publish("evolution", None, kind=kind, target=target, status=status, reason=reason[:200])

    def history(self, n: int = 30) -> list[dict]:
        return self.db.query("SELECT * FROM evolutions ORDER BY id DESC LIMIT ?", (n,))
