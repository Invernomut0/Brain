"""Results: what Brain produced and how far it got.

* artifacts: workspace files worth showing (pages, reports, data, images). Agents publish them with `publish_artifact`;
  files an agent wrote while working on a goal are also registered automatically when the goal ends.
* tool runs: the full output of every tool call (the event feed only keeps a short preview).
* progress: percentage toward the root goal with a summary and milestones. Agents report it with `report_progress`;
  the status report and the end of every goal add entries too, so the timeline is never empty.
"""
from __future__ import annotations

import json
import mimetypes
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .core import Brain

MAX_OUTPUT = 20_000  # characters stored per tool run
MAX_RUNS = 600  # tool runs kept
MAX_FILE = 10 * 1024 * 1024  # largest artifact served
AUTO_PER_GOAL = 8
KINDS = {
    "html": {".html", ".htm"}, "markdown": {".md", ".markdown"}, "image": {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"},
    "json": {".json"}, "csv": {".csv", ".tsv"}, "text": {".txt", ".log"}, "pdf": {".pdf"},
    "code": {".py", ".js", ".ts", ".sh", ".css", ".yaml", ".yml", ".sql"},
}
AUTO_KINDS = {"html", "markdown", "image", "json", "csv", "text", "pdf"}  # code files are only shown when an agent publishes them
SOURCES = ("agent", "status", "system")


class ResultsError(ValueError):
    """Bad input from an agent or a client (missing file, percentage out of range...)."""


class ArtifactTooLarge(ResultsError):
    pass


def kind_of(path: Path) -> str:
    ext = path.suffix.lower()
    return next((k for k, exts in KINDS.items() if ext in exts), "other")


class Results:
    def __init__(self, brain: "Brain"):
        self.b = brain

    # ------------------------------------------------------------------ files
    def resolve(self, rel: str) -> Path:
        """Workspace-relative path (also accepts /workspace/...) that must stay inside the workspace."""
        rel = str(rel).strip()
        for prefix in ("/workspace/", "workspace/", "./"):
            if rel.startswith(prefix):
                rel = rel[len(prefix):]
        base = self.b.settings.workspace_dir.resolve()
        p = (base / rel.lstrip("/")).resolve()
        if p == base or base not in p.parents:
            raise ResultsError("path outside the workspace: use a path relative to the workspace (e.g. 'report/summary.md')")
        return p

    def _rel(self, p: Path) -> str:
        return p.relative_to(self.b.settings.workspace_dir.resolve()).as_posix()

    # -------------------------------------------------------------- artifacts
    async def publish_artifact(
        self, path: str, title: str = "", description: str = "", agent: str | None = None, goal_id: int | None = None, auto: bool = False,
    ) -> dict:
        p = self.resolve(path)
        if not p.is_file():
            raise ResultsError(f"file not found in the workspace: {path}")
        st = p.stat()
        if st.st_size > MAX_FILE:
            raise ResultsError(f"file too large to show ({st.st_size // 1024} KB, limit {MAX_FILE // 1024} KB)")
        rel, title = self._rel(p), (title.strip() or p.name)[:120]
        now = time.time()
        old = self.b.db.one("SELECT id FROM artifacts WHERE path=?", (rel,))
        if old:
            if auto:  # a file an agent already presented keeps its title and description; only its size and date follow the file
                self.b.db.execute("UPDATE artifacts SET ts=?, size=?, mtime=? WHERE id=?", (now, st.st_size, st.st_mtime, old["id"]))
            else:
                self.b.db.execute(
                    "UPDATE artifacts SET ts=?, agent=?, goal_id=?, title=?, kind=?, description=?, size=?, mtime=?, auto=0 WHERE id=?",
                    (now, agent, goal_id, title, kind_of(p), description[:600], st.st_size, st.st_mtime, old["id"]),
                )
            aid = old["id"]
        else:
            aid = self.b.db.execute(
                "INSERT INTO artifacts(ts,agent,goal_id,title,path,kind,description,size,mtime,auto) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (now, agent, goal_id, title, rel, kind_of(p), description[:600], st.st_size, st.st_mtime, int(auto)),
            )
        art = self.artifact(aid)
        await self.b.bus.publish("artifact.published", agent, **{k: v for k, v in art.items() if k != "agent"})
        return art

    def artifact(self, aid: int) -> dict | None:
        return self.b.db.one("SELECT * FROM artifacts WHERE id=?", (aid,))

    def artifacts(self, limit: int = 200) -> list[dict]:
        rows = self.b.db.query("SELECT * FROM artifacts ORDER BY ts DESC, id DESC LIMIT ?", (limit,))
        base = self.b.settings.workspace_dir
        for r in rows:
            r["exists"] = (base / r["path"]).is_file()
        return rows

    def delete_artifact(self, aid: int) -> bool:
        gone = self.artifact(aid) is not None
        self.b.db.execute("DELETE FROM artifacts WHERE id=?", (aid,))
        return gone

    def open_artifact(self, aid: int) -> tuple[Path, str]:
        """File and media type of a registered artifact (only registered paths are ever served)."""
        art = self.artifact(aid)
        if not art:
            raise KeyError(aid)
        p = self.resolve(art["path"])
        if not p.is_file():
            raise FileNotFoundError(art["path"])
        if p.stat().st_size > MAX_FILE:
            raise ArtifactTooLarge("file too large")
        mime = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        if art["kind"] in ("markdown", "text", "csv", "code", "json") or mime.startswith("text/") and mime != "text/html":
            mime = "application/json" if art["kind"] == "json" else "text/plain; charset=utf-8"
        return p, mime

    async def collect(self, since: float, agent: str | None, goal_id: int | None) -> list[dict]:
        """Register the presentable files written since `since` that no agent has published (or has changed since)."""
        base = self.b.settings.workspace_dir
        if not base.exists():
            return []
        found: list[tuple[float, Path]] = []
        for p in base.rglob("*"):
            rel = p.relative_to(base).parts
            if not p.is_file() or rel[0] == "tools" or any(x.startswith(".") or x == "__pycache__" for x in rel):
                continue
            if kind_of(p) not in AUTO_KINDS:
                continue
            try:
                st = p.stat()
            except OSError:  # removed while scanning
                continue
            if st.st_mtime >= since and 0 < st.st_size <= MAX_FILE:
                known = self.b.db.one("SELECT mtime FROM artifacts WHERE path=?", ("/".join(rel),))
                if not known or known["mtime"] + 0.001 < st.st_mtime:
                    found.append((st.st_mtime, p))
        out = []
        for _, p in sorted(found, reverse=True)[:AUTO_PER_GOAL]:
            out.append(await self.publish_artifact(self._rel(p), "", "Written while working on the goal", agent, goal_id, auto=True))
        return out

    # -------------------------------------------------------------- tool runs
    def record_tool_run(self, agent: str, goal_id: int | None, tool: str, args: object, ok: bool, ms: int, output: object) -> int:
        out = output if isinstance(output, str) else json.dumps(output, ensure_ascii=False, default=str, indent=1)
        a = args if isinstance(args, str) else json.dumps(args, ensure_ascii=False, default=str)
        rid = self.b.db.execute(
            "INSERT INTO tool_runs(ts,agent,goal_id,tool,args,ok,ms,output) VALUES(?,?,?,?,?,?,?,?)",
            (time.time(), agent, goal_id, tool, a[:4000], int(ok), ms, out[:MAX_OUTPUT] + (f"\n… [{len(out) - MAX_OUTPUT} more characters]" if len(out) > MAX_OUTPUT else "")),
        )
        if rid % 50 == 0:
            self.b.db.execute("DELETE FROM tool_runs WHERE id <= ?", (rid - MAX_RUNS,))
        return rid

    def tool_runs(self, limit: int = 100, tool: str | None = None, agent: str | None = None, ok: bool | None = None) -> list[dict]:
        where, args = [], []
        if tool:
            where.append("tool=?"); args.append(tool)
        if agent:
            where.append("agent=?"); args.append(agent)
        if ok is not None:
            where.append("ok=?"); args.append(int(ok))
        sql = "SELECT id,ts,agent,goal_id,tool,ok,ms,substr(output,1,160) AS preview,length(output) AS size FROM tool_runs"
        rows = self.b.db.query(sql + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY id DESC LIMIT ?", (*args, limit))
        for r in rows:
            r["ok"] = bool(r["ok"])
        return rows

    def tool_run(self, rid: int) -> dict | None:
        r = self.b.db.one("SELECT * FROM tool_runs WHERE id=?", (rid,))
        if r:
            r["ok"] = bool(r["ok"])
        return r

    def tool_names(self) -> list[str]:
        return [r["tool"] for r in self.b.db.query("SELECT tool, COUNT(*) c FROM tool_runs GROUP BY tool ORDER BY c DESC")]

    # --------------------------------------------------------------- progress
    async def report_progress(
        self, percent: float, summary: str, milestones: list | None = None, agent: str | None = None,
        goal_id: int | None = None, source: str = "agent",
    ) -> dict:
        try:
            pct = float(percent)
        except (TypeError, ValueError) as e:
            raise ResultsError("percent must be a number between 0 and 100") from e
        if not 0 <= pct <= 100:
            raise ResultsError("percent must be between 0 and 100 (progress toward the ROOT goal)")
        if source not in SOURCES:
            raise ResultsError(f"unknown source {source}")
        summary = " ".join(str(summary).split())[:500]
        if not summary:
            raise ResultsError("a one-line summary is required")
        ms = []
        for m in (milestones or [])[:12]:
            if isinstance(m, dict) and str(m.get("title", "")).strip():
                ms.append({"title": str(m["title"]).strip()[:140], "done": bool(m.get("done"))})
            elif isinstance(m, str) and m.strip():
                ms.append({"title": m.strip()[:140], "done": False})
        pid = self.b.db.execute(
            "INSERT INTO progress(ts,agent,goal_id,percent,summary,milestones,source) VALUES(?,?,?,?,?,?,?)",
            (time.time(), agent, goal_id, round(pct, 1), summary, json.dumps(ms, ensure_ascii=False), source),
        )
        entry = self.progress_entry(pid)
        await self.b.bus.publish("progress.update", agent, **{k: v for k, v in entry.items() if k != "agent"})
        return entry

    def latest_progress(self) -> dict | None:
        r = self.b.db.one("SELECT id FROM progress ORDER BY id DESC LIMIT 1")
        return self.progress_entry(r["id"]) if r else None

    def progress_entry(self, pid: int) -> dict:
        r = self.b.db.one("SELECT * FROM progress WHERE id=?", (pid,)) or {}
        r["milestones"] = json.loads(r.get("milestones") or "[]")
        return r

    def progress_history(self, limit: int = 300) -> list[dict]:
        rows = self.b.db.query("SELECT * FROM progress ORDER BY id DESC LIMIT ?", (limit,))
        for r in rows:
            r["milestones"] = json.loads(r["milestones"] or "[]")
        return list(reversed(rows))

    def progress(self) -> dict:
        """Latest percentage and the newest milestone list, plus the whole timeline."""
        history = self.progress_history()
        latest = history[-1] if history else None
        with_ms = next((h for h in reversed(history) if h["milestones"]), None)
        c = {r["status"]: r["c"] for r in self.b.db.query("SELECT status, COUNT(*) c FROM goals WHERE parent_id IS NOT NULL GROUP BY status")}
        return {"latest": latest, "milestones": with_ms["milestones"] if with_ms else [], "history": history, "goals": c}

    async def goal_finished(self, goal: dict, ok: bool, summary: str) -> dict:
        """System entry at the end of every goal: the percentage carries forward, or is estimated from the goal counts."""
        c = self.progress()
        explicit = next((h for h in reversed(c["history"]) if h["source"] != "system"), None)
        counts = c["goals"]
        done, failed = counts.get("done", 0), counts.get("failed", 0)
        open_ = counts.get("pending", 0) + counts.get("active", 0)
        total = done + failed + open_
        pct = explicit["percent"] if explicit else (round(100 * done / total, 1) if total else 0.0)
        line = f"Goal #{goal['id']} {'succeeded' if ok else 'failed'}: {goal['title'][:80]}. {done} done, {failed} failed, {open_} queued."
        return await self.report_progress(pct, line, None, None, goal["id"], source="system")
