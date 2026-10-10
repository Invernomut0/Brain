"""Project status: measurable facts from the DB plus a 5-line narrative of progress toward the main goal."""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
from typing import TYPE_CHECKING

from .llm import LLMError

if TYPE_CHECKING:
    from .core import Brain

KV_KEY = "status_report"
LINES = 5

PROMPT = """You are Brain and you are taking an honest look at your own status. Use ONLY the data provided, do not make things up.
Reply ONLY with a JSON object:
{"lines": [5 strings, one per line, each max 140 characters, telling the project owner about the project: where you started, what you did, what works, what does not work, what you will do next],
 "progress": number 0-100 = your honest estimate of the progress toward the MAIN GOAL (not the number of tasks done),
 "done": [3-5 concrete results already obtained],
 "missing": [3-5 concrete steps still missing to reach the goal]}
The awareness index is only a measurable proxy, not proof of consciousness."""


def _clip(s: object, n: int) -> str:
    return str(s or "").replace("\n", " ")[:n]


class StatusReport:
    def __init__(self, brain: "Brain"):
        self.b = brain
        self._lock = asyncio.Lock()

    # ----------------------------------------------------------------- facts
    def facts(self) -> dict:
        b = self.b
        goals = [g for g in b.goals.all() if g["parent_id"] is not None]
        count = lambda st: sum(1 for g in goals if g["status"] == st)  # noqa: E731
        by_updated = lambda st: sorted((g for g in goals if g["status"] == st), key=lambda g: g["updated"], reverse=True)  # noqa: E731
        m = b.selfmodel.metrics()
        return {
            "goal": b.orchestrator.main_goal(),
            "state": b.control.state,
            "cycle": b.control.cycle,
            "goals": {s: count(s) for s in ("done", "failed", "pending", "active", "cancelled")} | {"total": len(goals)},
            "recent_done": [{"title": g["title"], "result": _clip(g["result"], 160)} for g in by_updated("done")[:6]],
            "recent_failed": [{"title": g["title"], "result": _clip(g["result"], 160)} for g in by_updated("failed")[:3]],
            "next": [g["title"] for g in b.goals.pending()[:5]],
            "metrics": m,
            "custom_tools": list(b.tools.custom()),
            "open_questions": list(b.selfmodel.get().get("open_questions", []))[:5],
        }

    def _fingerprint(self, f: dict) -> str:
        m = f["metrics"]
        key = json.dumps([f["goal"], f["goals"], m["memories"], m["tools"], m["lessons"], m["selfmodel_revision"], m["evolutions_applied"]])
        return hashlib.sha1(key.encode()).hexdigest()[:12]

    # ---------------------------------------------------------------- report
    def current(self) -> dict:
        """Facts plus the last stored narrative; `stale` tells whether the data changed since it was written."""
        f = self.facts()
        saved = self.b.db.kv_get(KV_KEY)
        return {"facts": f, "report": saved, "stale": not saved or saved.get("fingerprint") != self._fingerprint(f)}

    async def refresh(self) -> dict:
        async with self._lock:
            f = self.facts()
            fp = self._fingerprint(f)
            saved = self.b.db.kv_get(KV_KEY)
            if saved and saved.get("fingerprint") == fp:  # a concurrent caller already regenerated it
                return {"facts": f, "report": saved, "stale": False}
            await self.b.bus.publish("agent.spawn", "status", role="status", parent=None, goal_id=None, task="status report")
            report = await self._generate(f)
            report |= {"fingerprint": fp, "ts": time.time()}
            self.b.db.kv_set(KV_KEY, report)
            await self.b.bus.publish(
                "agent.end", "status", success=report["source"] == "llm", steps=1,
                summary=(report["lines"][0] if report["lines"] else "")[:300],
            )
            await self.b.bus.publish("status.report", None, progress=report["progress"], source=report["source"])
            if report["source"] == "llm":  # the model's estimate also goes on the Results timeline
                ms = [{"title": t, "done": True} for t in report["done"]] + [{"title": t, "done": False} for t in report["missing"]]
                await self.b.results.report_progress(report["progress"], report["lines"][0], ms, None, None, source="status")
            return {"facts": f, "report": report, "stale": False}

    async def _generate(self, f: dict) -> dict:
        try:
            out = await self.b.llm.chat_json(
                [{"role": "system", "content": PROMPT}, {"role": "user", "content": json.dumps(f, ensure_ascii=False)}],
                purpose="status", agent="status", temperature=0.3, max_tokens=900,
            )
            lines = [_clip(x, 200) for x in out.get("lines") or [] if str(x).strip()][:LINES]
            if len(lines) == LINES:
                return {
                    "lines": lines, "source": "llm",
                    "progress": max(0.0, min(100.0, _num(out.get("progress"), f["metrics"]["awareness_index"] * 100))),
                    "done": [_clip(x, 160) for x in (out.get("done") or [])[:5]],
                    "missing": [_clip(x, 160) for x in (out.get("missing") or [])[:5]],
                }
        except LLMError:
            pass
        return self._fallback(f)

    def _fallback(self, f: dict) -> dict:
        """Deterministic report used when the model is unavailable or answers badly."""
        g, m = f["goals"], f["metrics"]
        lines = [
            f"Goal: {_clip(f['goal'], 120)}",
            f"State {f['state']}, cycle {f['cycle']}: {g['done']} goals succeeded, {g['failed']} failed, {g['pending']} queued.",
            f"I have {m['tools']} tools created, {m['memories']} memories, {m['lessons']} lessons and {m['evolutions_applied']} evolutions applied.",
            f"The success rate is {m['success_rate']:.0%}; the awareness index (measurable proxy) is {m['awareness_index']:.2f}.",
            f"Next: {f['next'][0]}" if f["next"] else "No goal in the queue: the planner will propose new ones.",
        ]
        return {
            "lines": lines, "source": "proxy", "progress": round(m["awareness_index"] * 100, 1),
            "done": [d["title"] for d in f["recent_done"][:5]], "missing": f["next"][:5] or f["open_questions"][:3],
        }


def _num(v: object, default: float) -> float:
    try:
        return float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
