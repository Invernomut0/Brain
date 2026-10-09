"""Saved projects: named snapshots of Brain's whole state that can be restored later or swapped for a new project.

A snapshot (projects/<id>/) holds the database (goals, memories, journal, lessons, self-model, events, prompt versions),
the markdown wiki, the sandbox workspace (agent-made tools and files) and the evolvable prompts/hooks/tests.
"""
from __future__ import annotations

import asyncio
import json
import shutil
import time
from pathlib import Path
from typing import TYPE_CHECKING

from . import __version__
from .wiki import slugify as _slugify

if TYPE_CHECKING:
    from .core import Brain

NAME_KEY = "project_name"
DEFAULT_NAME = "Untitled project"
EVOLVABLE_DIRS = ("prompts", "hooks", "tests")
IGNORE = shutil.ignore_patterns("__pycache__", ".pytest_cache", "*.pyc")


class ProjectError(ValueError):
    """The request cannot be honoured (bad name, name already taken...)."""


class ProjectNotFound(KeyError):
    pass


def _pid(name: str) -> str:
    """Project id = filesystem-safe slug of the name (same name -> same project)."""
    return _slugify(name).strip("-") or "project"


def _clean(name: str | None) -> str:
    name = " ".join(str(name or "").split())[:60]
    if not name:
        raise ProjectError("a project name is required")
    return name


def _copy_tree(src: Path, dst: Path) -> None:
    shutil.rmtree(dst, ignore_errors=True)
    if src.exists():
        shutil.copytree(src, dst, ignore=IGNORE)
    else:
        dst.mkdir(parents=True, exist_ok=True)


def _size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


class Projects:
    def __init__(self, brain: "Brain"):
        self.b = brain
        self.dir: Path = brain.settings.projects_dir
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ queries
    def current_name(self) -> str:
        return self.b.db.kv_get(NAME_KEY) or DEFAULT_NAME

    def current(self) -> dict:
        name = self.current_name()
        pid = _pid(name)
        meta = self._read_meta(pid)
        return {"name": name, "id": pid, "saved": meta is not None, "saved_at": meta["saved_at"] if meta else None}

    def list(self) -> list[dict]:
        cur = _pid(self.current_name())
        out = []
        if self.dir.exists():
            for d in self.dir.iterdir():
                meta = self._read_meta(d.name) if d.is_dir() and not d.name.startswith(".") else None
                if meta:
                    out.append({**meta, "current": meta["id"] == cur})
        return sorted(out, key=lambda m: m["saved_at"], reverse=True)

    def _path(self, pid: str) -> Path:
        if _pid(pid) != pid or self._read_meta(pid) is None:
            raise ProjectNotFound(pid)
        return self.dir / pid

    def _read_meta(self, pid: str) -> dict | None:
        try:
            return json.loads((self.dir / pid / "meta.json").read_text())
        except (OSError, ValueError):
            return None

    def _meta(self, pid: str, name: str) -> dict:
        b = self.b
        q = lambda sql: b.db.one(sql)["c"]  # noqa: E731
        by_status = {r["status"]: r["c"] for r in b.db.query("SELECT status, COUNT(*) c FROM goals WHERE parent_id IS NOT NULL GROUP BY status")}
        return {
            "id": pid, "name": name, "saved_at": time.time(), "app_version": __version__, "cycle": b.control.cycle,
            "main_goal": ((b.goals.root() or {}).get("description") or "")[:300],
            "goals": {"done": by_status.get("done", 0), "failed": by_status.get("failed", 0), "total": sum(by_status.values())},
            "memories": q("SELECT COUNT(*) c FROM memories"), "lessons": len(b.lessons.all()),
            "tools": q("SELECT COUNT(*) c FROM tools WHERE status='active'"), "wiki_pages": q("SELECT COUNT(*) c FROM wiki_pages"),
        }

    # --------------------------------------------------------------------- save
    async def save(self, name: str | None = None) -> dict:
        """Snapshot the live state under `name` (default: the current project's name; an existing snapshot is replaced)."""
        async with self._lock:
            return await self._save(name)

    async def _save(self, name: str | None) -> dict:
        b = self.b
        name = _clean(name) if name else self.current_name()
        pid = _pid(name)
        b.db.kv_set(NAME_KEY, name)  # stored inside the snapshot too
        meta = self._meta(pid, name)
        tmp, final = self.dir / f".{pid}.tmp", self.dir / pid
        await asyncio.to_thread(self._write_snapshot, tmp, final, meta)
        await b.bus.publish("project.changed", None, name=name, id=pid, action="saved")
        return meta

    def _write_snapshot(self, tmp: Path, final: Path, meta: dict) -> None:
        b = self.b
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True)
        b.db.backup_to(tmp / "brain.db")
        _copy_tree(b.wiki.dir, tmp / "wiki")
        _copy_tree(b.settings.workspace_dir, tmp / "workspace")
        for sub in EVOLVABLE_DIRS:
            _copy_tree(b.settings.evolvable_dir / sub, tmp / "evolvable" / sub)
        meta["size_bytes"] = _size(tmp)
        (tmp / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))
        shutil.rmtree(final, ignore_errors=True)
        tmp.rename(final)

    # --------------------------------------------------------------------- load
    async def load(self, pid: str, save_current: bool = True) -> dict:
        """Replace the live state with a saved project (optionally saving the current one first)."""
        async with self._lock:
            src = self._path(pid)
            meta = self._read_meta(pid)
            if save_current and _pid(self.current_name()) != pid:
                await self._save(None)
            b = self.b
            await b._halt_everything()
            await b.sandbox.close()  # releases the workspace mount before it is replaced
            self._restore(src)
            await b.evolution.commit_all(f"evolve: restore project {meta['name']}")
            b.db.kv_set(NAME_KEY, meta["name"])
            await b._fresh_runtime(int(meta.get("cycle", 0)))
            await b._bootstrap()
            await b.wiki.reload()
            await b.bus.publish("system.reset", None)
            await b.bus.publish("project.changed", None, name=meta["name"], id=pid, action="loaded")
            if b.settings.autostart:
                await b.start()
            return meta

    def _restore(self, src: Path) -> None:
        """Synchronous on purpose: nothing else may touch the files or the database while they are swapped."""
        b = self.b
        b.db.restore_from(src / "brain.db")
        _copy_tree(src / "wiki", b.wiki.dir)
        ws = b.settings.workspace_dir
        for child in ws.iterdir():
            shutil.rmtree(child) if child.is_dir() else child.unlink()
        if (src / "workspace").exists():
            shutil.copytree(src / "workspace", ws, ignore=IGNORE, dirs_exist_ok=True)
        (ws / "tools").mkdir(parents=True, exist_ok=True)
        for sub in EVOLVABLE_DIRS:
            _copy_tree(src / "evolvable" / sub, b.settings.evolvable_dir / sub)

    # ---------------------------------------------------------------------- new
    async def new(self, name: str, main_goal: str | None = None, save_current: bool = True, owner: str | None = None) -> dict:
        """Start from scratch (factory state) as a new, named project; the previous one is optionally saved first."""
        async with self._lock:
            name = _clean(name)
            if (self.dir / _pid(name)).exists():
                raise ProjectError(f"a project named '{name}' already exists: load it, or pick another name")
            if save_current:
                await self._save(None)
            await self.b.reset()
            self.b.db.kv_set(NAME_KEY, name)
            if owner and owner.strip():
                await self.b.set_owner(owner)
            if main_goal and main_goal.strip():
                await self.b.set_main_goal(main_goal.strip(), True)
            return await self._save(name)

    # ------------------------------------------------------------------- delete
    def delete(self, pid: str) -> None:
        shutil.rmtree(self._path(pid))
