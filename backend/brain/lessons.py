"""Lessons learned from errors: persisted, deduplicated, and injected into future prompts."""
from __future__ import annotations

import re
import time

from .bus import EventBus
from .db import Database

MAX_LESSONS = 60
MAX_FAILURES = 40


def _tokens(s: str) -> set[str]:
    return set(re.findall(r"\w{3,}", s.lower()))


class Lessons:
    def __init__(self, db: Database, bus: EventBus):
        self.db, self.bus = db, bus

    def all(self) -> list[dict]:
        return self.db.kv_get("lessons", [])

    def count(self) -> int:
        return len(self.all())

    async def add(self, text: str, kind: str = "fix") -> bool:
        """Store a lesson; a near-duplicate only bumps its counter. Returns True if it is new."""
        text = " ".join(text.split())[:400]
        if len(text) < 12:
            return False
        items = self.all()
        toks = _tokens(text)
        for it in items:
            other = _tokens(it["text"])
            if toks and other and len(toks & other) / len(toks | other) >= 0.6:
                it["count"] += 1
                it["ts"] = time.time()
                self.db.kv_set("lessons", items)
                await self.bus.publish("lesson.learned", None, text=it["text"], kind=it["kind"], count=it["count"], new=False)
                return False
        items.append({"text": text, "kind": kind, "count": 1, "ts": time.time()})
        if len(items) > MAX_LESSONS:
            items.sort(key=lambda i: (i["count"], i["ts"]), reverse=True)
            items = items[:MAX_LESSONS]
        self.db.kv_set("lessons", items)
        await self.bus.publish("lesson.learned", None, text=text, kind=kind, count=1, new=True)
        return True

    def top(self, n: int = 8) -> list[str]:
        items = sorted(self.all(), key=lambda i: (i["count"], i["ts"]), reverse=True)
        return [i["text"] for i in items[:n]]

    def render(self, n: int = 8) -> str:
        top = self.top(n)
        return "\n".join(f"- {t}" for t in top) if top else ""

    def relevant(self, task: str, k: int = 3) -> list[str]:
        """Lessons that share vocabulary with the task (plus the most repeated format rule); the most frequent ones if none match."""
        items = self.all()
        want = _tokens(task)
        scored = []
        for it in items:
            toks = _tokens(it["text"])
            overlap = len(want & toks) / (len(toks) or 1)
            scored.append((overlap + 0.05 * min(it["count"], 5), overlap, it))
        scored.sort(key=lambda s: s[0], reverse=True)
        picked = [s[2] for s in scored if s[1] > 0.08][:k]
        fmt = next((i for i in sorted(items, key=lambda i: -i["count"]) if i["kind"] == "format" and i not in picked), None)
        if fmt:
            picked.append(fmt)
        if not picked:
            picked = sorted(items, key=lambda i: (i["count"], i["ts"]), reverse=True)[:k]
        return [i["text"] for i in picked]

    # -- failure log, distilled by the Reflector --------------------------
    def log_failure(self, tool: str, args: str, error: str, resolved: bool = False) -> None:
        log = self.db.kv_get("failures", [])
        log.append({"tool": tool, "args": args[:200], "error": error[:300], "resolved": resolved, "ts": time.time()})
        self.db.kv_set("failures", log[-MAX_FAILURES:])

    def mark_resolved(self, tool: str) -> None:
        log = self.db.kv_get("failures", [])
        for f in reversed(log):
            if f["tool"] == tool and not f["resolved"]:
                f["resolved"] = True
                break
        self.db.kv_set("failures", log)

    def recent_failures(self, n: int = 12) -> list[dict]:
        return self.db.kv_get("failures", [])[-n:]
