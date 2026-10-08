"""Goal tree persistence."""
from __future__ import annotations

import time

from .bus import EventBus
from .db import Database


class GoalStore:
    def __init__(self, db: Database, bus: EventBus):
        self.db, self.bus = db, bus

    async def add(
        self, title: str, description: str = "", parent_id: int | None = None,
        priority: float = 0.5, expected_success: float | None = None, role: str = "executor",
        status: str = "pending",
    ) -> int:
        now = time.time()
        gid = self.db.execute(
            "INSERT INTO goals(parent_id,title,description,priority,expected_success,created,updated,role,status) VALUES(?,?,?,?,?,?,?,?,?)",
            (parent_id, title[:200], description[:2000], priority, expected_success, now, now, role, status),
        )
        if expected_success is not None:
            self.db.execute("INSERT OR REPLACE INTO predictions(goal_id,p,ts) VALUES(?,?,?)", (gid, expected_success, now))
        await self.bus.publish("goal.update", None, **self.get(gid))
        return gid

    def get(self, gid: int) -> dict:
        return self.db.one("SELECT * FROM goals WHERE id=?", (gid,)) or {}

    async def set_status(self, gid: int, status: str, result: str | None = None) -> None:
        self.db.execute(
            "UPDATE goals SET status=?, result=COALESCE(?,result), updated=?, attempts=attempts+? WHERE id=?",
            (status, result, time.time(), 1 if status in ("done", "failed") else 0, gid),
        )
        await self.bus.publish("goal.update", None, **self.get(gid))

    async def requeue(self, gid: int, feedback: str) -> None:
        g = self.get(gid)
        self.db.execute(
            "UPDATE goals SET status='pending', description=?, attempts=attempts+1, updated=? WHERE id=?",
            ((g["description"] + f"\n[Tentativo precedente fallito: {feedback}]")[:2000], time.time(), gid),
        )
        await self.bus.publish("goal.update", None, **self.get(gid))

    def all(self) -> list[dict]:
        return self.db.query("SELECT * FROM goals ORDER BY id")

    def root(self) -> dict | None:
        return self.db.one("SELECT * FROM goals WHERE parent_id IS NULL ORDER BY id LIMIT 1")

    def pending(self) -> list[dict]:
        return self.db.query(
            "SELECT * FROM goals WHERE status='pending' AND parent_id IS NOT NULL ORDER BY priority DESC, id"
        )

    def summary(self, limit: int = 25) -> str:
        rows = self.all()[-limit:]
        return "\n".join(
            f"#{g['id']} [{g['status']}] (p={g['priority']:.1f}) {g['title']}"
            + (f" -> {g['result'][:100]}" if g.get("result") else "")
            for g in rows
        ) or "(nessun obiettivo)"
