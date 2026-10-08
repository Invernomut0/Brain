"""In-process event bus: persists events and fans them out to WebSocket subscribers."""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, asdict
from typing import Any

from .db import Database

# High-frequency events are streamed to the UI but never stored.
EPHEMERAL = {"agent.stream", "system.metrics", "llm.tokens"}


@dataclass
class Event:
    seq: int
    ts: float
    type: str
    agent: str | None
    data: dict[str, Any]

    def to_dict(self) -> dict:
        return asdict(self)


class EventBus:
    def __init__(self, db: Database):
        self.db = db
        self._subs: set[asyncio.Queue] = set()
        self._ephemeral_seq = 0

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=2000)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)

    async def publish(self, type: str, agent: str | None = None, **data: Any) -> Event:
        ts = time.time()
        if type in EPHEMERAL:
            self._ephemeral_seq -= 1
            seq = self._ephemeral_seq
        else:
            seq = self.db.execute(
                "INSERT INTO events(ts,type,agent,data) VALUES(?,?,?,?)",
                (ts, type, agent, json.dumps(data, ensure_ascii=False, default=str)),
            )
        ev = Event(seq, ts, type, agent, data)
        for q in list(self._subs):
            if q.full():
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            q.put_nowait(ev)
        return ev

    def recent(self, limit: int = 200, types: list[str] | None = None) -> list[dict]:
        sql = "SELECT seq,ts,type,agent,data FROM events"
        params: list[Any] = []
        if types:
            sql += " WHERE type IN (%s)" % ",".join("?" * len(types))
            params += types
        sql += " ORDER BY seq DESC LIMIT ?"
        params.append(limit)
        rows = self.db.query(sql, params)
        for r in rows:
            r["data"] = json.loads(r["data"])
        return list(reversed(rows))
