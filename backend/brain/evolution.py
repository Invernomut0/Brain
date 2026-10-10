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

PROMPT_MIN, PROMPT_MAX = 80, 1600
PATH_RE = re.compile(r"\b[\w-]+/[\w./-]*\.(?:csv|json|parquet|py|txt|md|html|xlsx|db)\b")
CONTRACT_WORD = {"planner": "goals", "critic": "verdict", "reflector": "journal"}


def validate_prompt(role: str, text: str) -> str | None:
    """Why a prompt must not be installed (None when it is fine). Role prompts stay short, generic and in their own contract:
    task-specific protocols, other roles' output formats and pasted hook code are what made executors lose focus."""
    if role not in DEFAULTS:
        return f"unknown role {role}"
    if not PROMPT_MIN <= len(text) <= PROMPT_MAX:
        return f"prompt length must be {PROMPT_MIN}-{PROMPT_MAX} chars (it is {len(text)}): keep role prompts short and generic"
    if role in CONTRACT_WORD and ("JSON" not in text or CONTRACT_WORD[role] not in text):
        return f"prompt must keep the {role} JSON output contract (with the '{CONTRACT_WORD[role]}' field)"
    if role != "planner" and ('"rationale"' in text or '"goals"' in text):
        return "prompt contains the planner's output format: each role keeps only its own contract"
    if re.search(r"^\s*def (prioritize|build_context)\b", text, re.M) or "Hook prioritize" in text:
        return "prompt contains pasted hook code"
    if PATH_RE.search(text):
        return "prompt mentions specific workspace files (%s): role prompts must be task-independent" % PATH_RE.search(text).group(0)
    if text.upper().count("MANDATORY") > 1:
        return "prompt stacks several 'MANDATORY' protocols: keep one short, general rule per change"
    return None


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

    async def commit_all(self, message: str) -> str:
        """Commit whatever is currently in the evolvable zone (used after a project restore)."""
        return await self._commit(message)

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
    async def reset(self) -> None:
        """Restore the factory prompts/hooks/tests (committed to git, so history is preserved)."""
        for sub in ("prompts", "hooks", "tests"):
            for f in (self.s.evolvable_dir / sub).glob("*"):
                if f.is_file():
                    f.unlink()
        self.hook_failures.clear()
        await self.seed()

    # ---- prompts -------------------------------------------------------------
    def prompt(self, role: str) -> str:
        p = self.s.evolvable_dir / "prompts" / f"{role}.md"
        if p.exists():
            return p.read_text()
        return DEFAULTS.get(role, DEFAULTS["executor"])

    def record_run(self, role: str, success: bool, focus: float | None = None) -> None:
        self.db.execute(
            "UPDATE prompt_versions SET runs=runs+1, successes=successes+?, focus_sum=focus_sum+?, focus_n=focus_n+? WHERE role=? AND active=1",
            (1 if success else 0, focus or 0.0, 0 if focus is None else 1, role),
        )

    async def audit_prompts(self) -> list[str]:
        """Put back the default of every role prompt that breaks the rules (e.g. evolved before they existed)."""
        fixed = []
        for role, default in DEFAULTS.items():
            p = self.s.evolvable_dir / "prompts" / f"{role}.md"
            problem = validate_prompt(role, p.read_text()) if p.exists() else None
            if problem:
                await self.reset_prompt(role, f"audit: {problem}"[:200])
                fixed.append(role)
        return fixed

    async def reset_prompt(self, role: str, reason: str = "reset to default") -> None:
        (self.s.evolvable_dir / "prompts" / f"{role}.md").write_text(DEFAULTS[role])
        sha = await self._commit(f"evolve: reset prompt {role}: {reason[:80]}")
        cur = self.db.one("SELECT version FROM prompt_versions WHERE role=? ORDER BY version DESC LIMIT 1", (role,))
        self.db.execute("UPDATE prompt_versions SET active=0 WHERE role=?", (role,))
        self.db.execute(
            "INSERT INTO prompt_versions(role,version,sha,reason,created) VALUES(?,?,?,?,?)",
            (role, (cur["version"] + 1) if cur else 1, sha, reason[:300], time.time()),
        )
        await self._log("prompt", role, sha, "reset", reason, "back to the default prompt")

    async def propose_prompt(self, role: str, text: str, reason: str) -> dict:
        problem = validate_prompt(role, text)
        if problem:
            return {"ok": False, "error": problem}
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
        """Revert a prompt version whose success rate or focus (on-goal steps) is clearly worse than its predecessor."""
        rolled = []
        for row in self.db.query("SELECT * FROM prompt_versions WHERE active=1 AND version>1 AND runs>=5"):
            prev = self.db.one(
                "SELECT * FROM prompt_versions WHERE role=? AND version=?", (row["role"], row["version"] - 1)
            )
            if not prev or prev["runs"] < 3:
                continue
            worse_success = row["successes"] / row["runs"] < prev["successes"] / prev["runs"] - 0.15
            worse_focus = (
                row["focus_n"] >= 4 and prev["focus_n"] >= 3
                and row["focus_sum"] / row["focus_n"] < prev["focus_sum"] / prev["focus_n"] - 0.15
            )
            if worse_success or worse_focus:
                why = "focus regression" if worse_focus and not worse_success else "performance regression"
                await self._git("checkout", prev["sha"], "--", f"evolvable/prompts/{row['role']}.md")
                sha = await self._commit(f"evolve: rollback prompt {row['role']} v{row['version']}")
                self.db.execute("UPDATE prompt_versions SET active=0 WHERE role=?", (row["role"],))
                self.db.execute("UPDATE prompt_versions SET active=1 WHERE id=?", (prev["id"],))
                await self._log("prompt", row["role"], sha, "rolled_back", why, f"v{row['version']}")
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
            stdin=json.dumps({"args": list(args)}, default=str), timeout=45,
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
