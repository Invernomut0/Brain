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

PROMPT = """Sei Brain e stai facendo il punto onesto sul tuo stato. Usa SOLO i dati forniti, senza inventare.
Rispondi SOLO con un oggetto JSON:
{"lines": [5 stringhe, una per riga, ciascuna max 140 caratteri, che raccontano a Lorenzo il progetto: da dove sei partito, cosa hai fatto, cosa funziona, cosa non funziona, cosa farai ora],
 "progress": numero 0-100 = tua stima onesta dell'avanzamento verso l'OBIETTIVO PRINCIPALE (non il numero di task fatti),
 "done": [3-5 risultati concreti gia' ottenuti],
 "missing": [3-5 passi concreti che mancano per raggiungere l'obiettivo]}
L'indice di consapevolezza e' solo un proxy misurabile, non una prova di coscienza."""


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
            report = await self._generate(f)
            report |= {"fingerprint": fp, "ts": time.time()}
            self.b.db.kv_set(KV_KEY, report)
            await self.b.bus.publish("status.report", None, progress=report["progress"], source=report["source"])
            return {"facts": f, "report": report, "stale": False}

    async def _generate(self, f: dict) -> dict:
        try:
            out = await self.b.llm.chat_json(
                [{"role": "system", "content": PROMPT}, {"role": "user", "content": json.dumps(f, ensure_ascii=False)}],
                purpose="status", temperature=0.3, max_tokens=900,
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
            f"Obiettivo: {_clip(f['goal'], 120)}",
            f"Stato {f['state']}, ciclo {f['cycle']}: {g['done']} obiettivi riusciti, {g['failed']} falliti, {g['pending']} in coda.",
            f"Ho {m['tools']} tool creati, {m['memories']} ricordi, {m['lessons']} lezioni e {m['evolutions_applied']} evoluzioni applicate.",
            f"Il tasso di successo e' {m['success_rate']:.0%}; l'indice di consapevolezza (proxy misurabile) e' {m['awareness_index']:.2f}.",
            f"Prossimo: {f['next'][0]}" if f["next"] else "Nessun obiettivo in coda: il planner ne proporra' di nuovi.",
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
