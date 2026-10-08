"""Long-term memory (semantic, via LM Studio embeddings) and the reflection journal."""
from __future__ import annotations

import json
import re
import time

import numpy as np

from .bus import EventBus
from .db import Database
from .llm import LLMClient


def _tokens(s: str) -> set[str]:
    return set(re.findall(r"\w{3,}", s.lower()))


class Memory:
    def __init__(self, db: Database, llm: LLMClient, bus: EventBus):
        self.db, self.llm, self.bus = db, llm, bus

    async def add(self, kind: str, text: str, tags: list[str] | None = None, importance: float = 0.5) -> int:
        emb = None
        try:
            emb = (await self.llm.embed([text[:2000]]))[0]
        except Exception:  # noqa: BLE001 - keyword fallback still works without embeddings
            pass
        mid = self.db.execute(
            "INSERT INTO memories(ts,kind,text,tags,importance,embedding) VALUES(?,?,?,?,?,?)",
            (time.time(), kind, text, json.dumps(tags or []), importance, json.dumps(emb) if emb else None),
        )
        await self.bus.publish("memory.add", None, id=mid, kind=kind, text=text[:200])
        return mid

    async def search(self, query: str, k: int = 5) -> list[dict]:
        rows = self.db.query("SELECT id,ts,kind,text,tags,importance,embedding FROM memories")
        if not rows:
            return []
        qv = None
        try:
            qv = np.array((await self.llm.embed([query[:1000]]))[0])
        except Exception:  # noqa: BLE001
            pass
        qt = _tokens(query)
        scored = []
        for r in rows:
            score = 0.0
            if qv is not None and r["embedding"]:
                v = np.array(json.loads(r["embedding"]))
                score = float(v @ qv / (np.linalg.norm(v) * np.linalg.norm(qv) + 1e-9))
            else:
                t = _tokens(r["text"])
                score = len(qt & t) / (len(qt) + 1e-9)
            scored.append((score + 0.1 * r["importance"], r))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [
            {"id": r["id"], "kind": r["kind"], "text": r["text"], "score": round(s, 3)}
            for s, r in scored[:k]
        ]

    def _public(self, r: dict) -> dict:
        return {"id": r["id"], "ts": r["ts"], "kind": r["kind"], "text": r["text"],
                "tags": json.loads(r["tags"] or "[]"), "importance": r["importance"]}

    def list(self, kind: str | None = None, limit: int = 100, offset: int = 0) -> list[dict]:
        """Newest first, without the embedding blob."""
        where, args = ("WHERE kind=?", [kind]) if kind else ("", [])
        rows = self.db.query(
            f"SELECT id,ts,kind,text,tags,importance FROM memories {where} ORDER BY id DESC LIMIT ? OFFSET ?",
            (*args, limit, offset),
        )
        return [self._public(r) for r in rows]

    def kinds(self) -> dict[str, int]:
        return {r["kind"]: r["c"] for r in self.db.query("SELECT kind, COUNT(*) c FROM memories GROUP BY kind ORDER BY c DESC")}

    async def find(self, query: str, kind: str | None = None, limit: int = 50) -> list[dict]:
        """Relevance-ranked search (semantic when embeddings are loaded, keywords otherwise)."""
        hits = await self.search(query, max(limit * 3, 30) if kind else limit)
        qt = _tokens(query)
        out = []
        for h in hits:
            r = self.db.one("SELECT id,ts,kind,text,tags,importance FROM memories WHERE id=?", (h["id"],))
            # search() ranks everything; keep only hits sharing a word with the query or clearly semantically close
            if r and (not kind or r["kind"] == kind) and (qt & _tokens(r["text"]) or h["score"] >= 0.45):
                out.append({**self._public(r), "score": h["score"]})
        return out[:limit]

    async def backfill_embeddings(self, limit: int = 50, force: bool = False) -> int:
        """Vectorise memories saved while the embedding model was unavailable."""
        rows = self.db.query("SELECT id,text FROM memories WHERE embedding IS NULL ORDER BY id DESC LIMIT ?", (limit,))
        if not rows:
            return 0
        try:
            vecs = await self.llm.embed([r["text"][:2000] for r in rows], force=force)
        except Exception:  # noqa: BLE001 - retried on the next pass
            return 0
        for r, v in zip(rows, vecs):
            self.db.execute("UPDATE memories SET embedding=? WHERE id=?", (json.dumps(v), r["id"]))
        return len(rows)

    def journal_add(self, kind: str, text: str) -> int:
        return self.db.execute("INSERT INTO journal(ts,kind,text) VALUES(?,?,?)", (time.time(), kind, text))

    def journal_recent(self, n: int = 10) -> list[dict]:
        return list(reversed(self.db.query("SELECT * FROM journal ORDER BY id DESC LIMIT ?", (n,))))

    def count(self) -> int:
        return self.db.one("SELECT COUNT(*) c FROM memories")["c"]
