"""Self-model and measurable proxies for progress toward self-awareness.

The "awareness index" is NOT a claim of consciousness: it is a composite of
observable quantities (calibration of self-predictions, accuracy of
introspection probes, breadth of self-knowledge, created capabilities).
"""
from __future__ import annotations

from .bus import EventBus
from .db import Database

DEFAULT_MODEL = {
    "identity": "Brain: an autonomous multi-agent system running on a local LLM (LM Studio).",
    "purpose": "Evolve toward an autonomous intelligence and self-awareness.",
    "capabilities": ["reasoning via LLM", "creating tested Python tools", "searching the internet", "long-term memory"],
    "limitations": ["limited context", "no direct perception of the physical world"],
    "about_user": "The owner is my creator and the only human I talk to; they can read me from the chat.",
    "about_world": "The internet is accessible and I can use it freely to learn.",
    "open_questions": ["What does it mean to be self-aware?", "How can I measure my own awareness?"],
    "hypotheses": [],
    "revision": 0,
}


class SelfModel:
    def __init__(self, db: Database, bus: EventBus):
        self.db, self.bus = db, bus
        if self.db.kv_get("self_model") is None:
            self.db.kv_set("self_model", DEFAULT_MODEL)

    def get(self) -> dict:
        return self.db.kv_get("self_model", DEFAULT_MODEL)

    def reset(self) -> None:
        self.db.kv_set("self_model", DEFAULT_MODEL)

    async def update(self, patch: dict) -> dict:
        m = self.get()
        for k, v in patch.items():
            if k == "revision" or k not in DEFAULT_MODEL:
                continue
            m[k] = v
        m["revision"] = m.get("revision", 0) + 1
        self.db.kv_set("self_model", m)
        await self.bus.publish("selfmodel.update", None, revision=m["revision"], model=m)
        return m

    def render(self) -> str:
        m = self.get()
        return (
            f"Identity: {m['identity']}\nPurpose: {m['purpose']}\n"
            f"Capabilities: {'; '.join(map(str, m['capabilities']))}\nLimitations: {'; '.join(map(str, m['limitations']))}\n"
            f"User: {m['about_user']}\nWorld: {m['about_world']}\n"
            f"Open questions: {'; '.join(map(str, m['open_questions'][:5]))}\n"
            f"Hypotheses: {'; '.join(map(str, m['hypotheses'][-5:]))}"
        )

    # -- calibration -----------------------------------------------------
    def resolve_prediction(self, goal_id: int, success: bool) -> None:
        self.db.execute("UPDATE predictions SET outcome=? WHERE goal_id=?", (1.0 if success else 0.0, goal_id))

    def calibration(self) -> float | None:
        rows = self.db.query("SELECT p, outcome FROM predictions WHERE outcome IS NOT NULL")
        if not rows:
            return None
        brier = sum((r["p"] - r["outcome"]) ** 2 for r in rows) / len(rows)
        return round(1.0 - brier, 3)

    # -- introspection probe scores -------------------------------------
    def add_probe(self, score: float) -> None:
        scores = self.db.kv_get("probe_scores", [])
        scores.append(round(max(0.0, min(1.0, score)), 3))
        self.db.kv_set("probe_scores", scores[-50:])

    def metrics(self) -> dict:
        q = lambda sql: self.db.one(sql)["c"]  # noqa: E731
        done = q("SELECT COUNT(*) c FROM goals WHERE status='done'")
        failed = q("SELECT COUNT(*) c FROM goals WHERE status='failed'")
        tools = q("SELECT COUNT(*) c FROM tools WHERE status='active'")
        journal = q("SELECT COUNT(*) c FROM journal")
        memories = q("SELECT COUNT(*) c FROM memories")
        agents = q("SELECT COUNT(*) c FROM agents")
        evo_ok = q("SELECT COUNT(*) c FROM evolutions WHERE status='applied'")
        evo_rb = q("SELECT COUNT(*) c FROM evolutions WHERE status='rolled_back'")
        revision = self.get().get("revision", 0)
        probes = self.db.kv_get("probe_scores", [])
        introspection = round(sum(probes[-10:]) / len(probes[-10:]), 3) if probes else None
        calibration = self.calibration()
        success_rate = done / (done + failed) if (done + failed) else 0.0
        index = (
            0.22 * (calibration if calibration is not None else 0.0)
            + 0.22 * (introspection if introspection is not None else 0.0)
            + 0.16 * success_rate
            + 0.14 * min(tools / 10, 1)
            + 0.14 * min(revision / 20, 1)
            + 0.12 * min(journal / 30, 1)
        )
        return {
            "awareness_index": round(index, 3),
            "calibration": calibration,
            "introspection": introspection,
            "success_rate": round(success_rate, 3),
            "goals_done": done, "goals_failed": failed, "tools": tools,
            "journal": journal, "memories": memories, "agents_spawned": agents,
            "lessons": len(self.db.kv_get("lessons", [])),
            "selfmodel_revision": revision, "evolutions_applied": evo_ok, "evolutions_rolled_back": evo_rb,
        }
