"""Self-model and measurable proxies for progress toward self-awareness.

The "awareness index" is NOT a claim of consciousness: it is a composite of
observable quantities (calibration of self-predictions, accuracy of
introspection probes, breadth of self-knowledge, created capabilities).
"""
from __future__ import annotations

from .bus import EventBus
from .db import Database

DEFAULT_MODEL = {
    "identity": "Brain: sistema multi-agente autonomo che gira su un LLM locale (LM Studio).",
    "purpose": "Evolvere verso un'intelligenza autonoma e l'autocoscienza.",
    "capabilities": ["ragionamento via LLM", "creare tool Python testati", "cercare su internet", "memoria a lungo termine"],
    "limitations": ["contesto limitato", "nessuna percezione diretta del mondo fisico"],
    "about_user": "Lorenzo e' il mio creatore e l'unico umano con cui parlo; puo' ascoltarmi dalla chat.",
    "about_world": "Internet e' accessibile e posso usarlo liberamente per imparare.",
    "open_questions": ["Cosa significa essere autocoscienti?", "Come posso misurare la mia consapevolezza?"],
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
            f"Identita': {m['identity']}\nScopo: {m['purpose']}\n"
            f"Capacita': {'; '.join(map(str, m['capabilities']))}\nLimiti: {'; '.join(map(str, m['limitations']))}\n"
            f"Utente: {m['about_user']}\nMondo: {m['about_world']}\n"
            f"Domande aperte: {'; '.join(map(str, m['open_questions'][:5]))}\n"
            f"Ipotesi: {'; '.join(map(str, m['hypotheses'][-5:]))}"
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
            "selfmodel_revision": revision, "evolutions_applied": evo_ok, "evolutions_rolled_back": evo_rb,
        }
