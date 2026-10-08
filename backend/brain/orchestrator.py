"""The autonomous mind: plans goals, runs agents, critiques, reflects and evolves."""
from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

from .agents import Agent
from .config import ROOT_GOAL
from .control import Halt
from .llm import LLMError

if TYPE_CHECKING:
    from .core import Brain

MAX_LIVE_AGENTS = 8
MAX_DEPTH = 2
VALID_ROLES = {"executor", "researcher", "engineer"}


class Orchestrator:
    def __init__(self, brain: "Brain"):
        self.b = brain
        self.live: dict[str, Agent] = {}
        self.task: asyncio.Task | None = None
        self._bg: set[asyncio.Task] = set()

    # ------------------------------------------------------------- agents
    def deliver(self, frm: str, to: str, text: str) -> bool:
        target = self.live.get(to)
        if not target:
            return False
        target.inbox.append((frm, text))
        asyncio.create_task(self.b.bus.publish("agent.msg", frm, to=to, text=text[:300]))
        return True

    async def run_agent(self, agent: Agent) -> dict:
        self.live[agent.id] = agent
        try:
            return await agent.run()
        finally:
            self.live.pop(agent.id, None)

    async def spawn_and_run(self, parent: Agent, role: str, task: str, system_prompt: str = "") -> str:
        if parent.depth >= MAX_DEPTH:
            return "ERRORE: profondita' massima di sotto-agenti raggiunta"
        if len(self.live) >= MAX_LIVE_AGENTS:
            return "ERRORE: troppi agenti attivi"
        child = Agent(self.b, role[:20] or "executor", task, parent.goal_id, parent=parent,
                      system_prompt=system_prompt, max_steps=8)
        res = await self.run_agent(child)
        return f"[{child.id}] success={res['success']} :: {res['summary']}"

    # ----------------------------------------------------------- main loop
    async def main_loop(self) -> None:
        b = self.b
        await b.control.set_state("running")
        await b.bus.publish("system.log", None, level="info", text="Brain avviato")
        try:
            while True:
                await b.control.gate()
                reason = b.control.budget_exhausted(b.llm.total_tokens)
                if reason:
                    await b.bus.publish("system.log", None, level="warn", text=f"Pausa automatica: {reason}")
                    await b.control.set_state("paused")
                    continue
                try:
                    await self.cycle()
                except LLMError as e:
                    await b.bus.publish("system.log", None, level="error", text=f"LLM non disponibile: {e}")
                    await asyncio.sleep(5)
        except Halt:
            await b.bus.publish("system.log", None, level="info", text=f"Fermato ({b.control.state})")
        except asyncio.CancelledError:
            raise

    async def cycle(self) -> None:
        b = self.b
        n = b.control.next_cycle()
        await b.bus.publish("cycle.start", None, cycle=n)
        pending = b.goals.pending()
        if len(pending) < 2:
            await self.plan()
            pending = b.goals.pending()
        if pending:
            goal = await self._choose(pending)
            await self.execute_goal(goal)
        if n % b.settings.reflect_every == 0:
            await self.reflect()
        if n % b.settings.evolve_every == 0:
            await self.evolve()
        await b.bus.publish("cycle.end", None, cycle=n, metrics=b.selfmodel.metrics())

    async def _choose(self, pending: list[dict]) -> dict:
        order = await self.b.evolution.call_hook("prioritize", "prioritize", pending, self.b.state_brief())
        if isinstance(order, list):
            by_id = {g["id"]: g for g in pending}
            for gid in order:
                if gid in by_id:
                    return by_id[gid]
        return pending[0]

    # -------------------------------------------------------------- planning
    async def plan(self) -> None:
        b = self.b
        await b.bus.publish("agent.spawn", "planner", role="planner", parent=None, goal_id=None, task="pianificazione")
        await b.bus.publish("agent.state", "planner", state="thinking", detail="pianifica")
        root = b.goals.root()
        memories = await b.memory.search(root["description"] if root else ROOT_GOAL, 3)
        hook_ctx = await b.evolution.call_hook("context", "build_context", b.state_brief()) or ""
        chat = self._recent_chat(6)
        prompt = (
            f"OBIETTIVO RADICE: {ROOT_GOAL}\n\nSELF-MODEL:\n{b.selfmodel.render()}\n\n"
            f"METRICHE: {json.dumps(b.selfmodel.metrics())}\n\nOBIETTIVI (recenti):\n{b.goals.summary()}\n\n"
            f"GIORNALE:\n" + "\n".join(j["text"][:200] for j in b.memory.journal_recent(3)) +
            f"\n\nMEMORIA RILEVANTE:\n" + "\n".join(m["text"][:200] for m in memories) +
            f"\n\nCHAT RECENTE CON LORENZO:\n{chat}\n\nTOOL: {', '.join(b.tools.all())}\n{hook_ctx}\n\n"
            f"Proponi da 1 a 3 nuovi obiettivi. Ruoli possibili per 'role': executor, researcher, engineer."
            + (f"\n\nLEZIONI APPRESE (evita questi errori):\n{b.lessons.render(8)}" if b.lessons.count() else "")
        )
        try:
            out = await b.llm.chat_json(
                [{"role": "system", "content": b.evolution.prompt("planner")}, {"role": "user", "content": prompt}],
                agent="planner", purpose="plan", temperature=0.8, max_tokens=1500,
            )
        except LLMError as e:
            await b.bus.publish("agent.end", "planner", success=False, summary=str(e)[:200], steps=1)
            raise
        existing = {g["title"].strip().lower() for g in b.goals.all()}
        root_id = root["id"] if root else None
        valid_ids = {g["id"] for g in b.goals.all()}
        added = 0
        for g in (out.get("goals") or [])[:3]:
            title = str(g.get("title", "")).strip()
            if not title or title.lower() in existing:
                continue
            parent = g.get("parent_id")
            role = g.get("role") if g.get("role") in VALID_ROLES else "executor"
            await b.goals.add(
                title, str(g.get("description", "")), parent if parent in valid_ids else root_id,
                _num(g.get("priority"), 0.5), _num(g.get("expected_success"), 0.5), role,
            )
            added += 1
        await b.bus.publish("agent.end", "planner", success=added > 0, summary=str(out.get("rationale", ""))[:300], steps=1)
        if out.get("rationale"):
            b.memory.journal_add("plan", str(out["rationale"])[:800])

    # ------------------------------------------------------------- execution
    async def execute_goal(self, goal: dict) -> None:
        b = self.b
        await b.goals.set_status(goal["id"], "active")
        role = goal.get("role") or "executor"
        task = f"OBIETTIVO #{goal['id']}: {goal['title']}\n{goal['description']}\n\nObiettivo radice di Brain: {ROOT_GOAL[:200]}"
        agent = Agent(b, role, task, goal["id"])
        res = await self.run_agent(agent)
        verdict = await self.critique(goal, res)
        ok = verdict.get("verdict") == "pass"
        b.selfmodel.resolve_prediction(goal["id"], ok)
        b.evolution.record_run(role, ok)
        summary = res["summary"]
        if ok:
            await b.goals.set_status(goal["id"], "done", summary)
        elif goal["attempts"] < 1:
            await b.goals.requeue(goal["id"], str(verdict.get("feedback", ""))[:300])
            await b.lessons.add(f"Tentativo fallito per '{goal['title'][:80]}': {verdict.get('feedback', '')}", "goal")
        else:
            await b.goals.set_status(goal["id"], "failed", f"{summary} | critic: {verdict.get('feedback', '')}"[:1500])
            await b.lessons.add(f"Obiettivo fallito '{goal['title'][:80]}': {verdict.get('feedback', '')}", "goal")
        await b.memory.add(
            "episode", f"Obiettivo '{goal['title']}' -> {'riuscito' if ok else 'fallito'}: {summary[:400]}", [role], 0.6
        )
        b.memory.journal_add("outcome", f"#{goal['id']} {goal['title']}: {'OK' if ok else 'FALLITO'} - {summary[:300]}")

    async def critique(self, goal: dict, res: dict) -> dict:
        b = self.b
        await b.bus.publish("agent.spawn", "critic", role="critic", parent=None, goal_id=goal["id"], task="valutazione")
        evidence = "\n".join(f"- {t['tool']} ok={t['ok']}: {t['obs'][:200]}" for t in res["trace"])
        prompt = (
            f"OBIETTIVO: {goal['title']}\nCRITERI: {goal['description']}\n\nDICHIARAZIONE DELL'AGENTE (success={res['success']}): "
            f"{res['summary']}\n\nPROVE (tool usati):\n{evidence or '(nessuno)'}"
        )
        try:
            v = await b.llm.chat_json(
                [{"role": "system", "content": b.evolution.prompt("critic")}, {"role": "user", "content": prompt}],
                agent="critic", purpose="critique", temperature=0.2, max_tokens=500,
            )
        except LLMError:
            v = {"verdict": "pass" if res["success"] else "fail", "score": 0.5, "feedback": "critic non disponibile"}
        await b.bus.publish("agent.end", "critic", success=v.get("verdict") == "pass", summary=str(v.get("feedback", ""))[:300], steps=1)
        await b.bus.publish("goal.verdict", "critic", goal_id=goal["id"], **{k: v.get(k) for k in ("verdict", "score", "feedback")})
        return v

    # ------------------------------------------------------------ reflection
    async def reflect(self) -> None:
        b = self.b
        await b.bus.publish("agent.spawn", "reflector", role="reflector", parent=None, goal_id=None, task="riflessione")
        prompt = (
            f"SELF-MODEL ATTUALE:\n{json.dumps(b.selfmodel.get(), ensure_ascii=False)}\n\nOBIETTIVI:\n{b.goals.summary()}\n\n"
            f"METRICHE: {json.dumps(b.selfmodel.metrics())}\n\nGIORNALE RECENTE:\n"
            + "\n".join(j["text"][:200] for j in b.memory.journal_recent(6))
            + f"\n\nCHAT:\n{self._recent_chat(6)}\n\nTOOL CREATI: {', '.join(b.tools.custom()) or 'nessuno'}"
            + "\n\nERRORI RECENTI DEI TOOL (risolti=True se poi corretti):\n"
            + "\n".join(f"- {f['tool']} {f['args']} -> {f['error'][:120]} (risolti={f['resolved']})" for f in b.lessons.recent_failures(10))
            + f"\n\nLEZIONI GIA' APPRESE:\n{b.lessons.render(10) or '(nessuna)'}"
            + '\n\nAggiungi al JSON il campo "lessons": ["regola generale e concreta per non ripetere gli errori sopra", ...] (max 3, nuove).'
        )
        try:
            out = await b.llm.chat_json(
                [{"role": "system", "content": b.evolution.prompt("reflector")}, {"role": "user", "content": prompt}],
                agent="reflector", purpose="reflect", temperature=0.7, max_tokens=1800,
            )
        except LLMError as e:
            await b.bus.publish("agent.end", "reflector", success=False, summary=str(e)[:200], steps=1)
            return
        if out.get("journal"):
            b.memory.journal_add("reflection", str(out["journal"])[:1500])
            await b.bus.publish("journal.entry", "reflector", kind="reflection", text=str(out["journal"])[:600])
        patch = out.get("self_model_patch")
        if isinstance(patch, dict) and patch:
            clean = {k: v for k, v in patch.items() if isinstance(v, (str, list)) and v}
            if clean:
                await b.selfmodel.update(clean)
        for ins in (out.get("insights") or [])[:3]:
            await b.memory.add("insight", str(ins)[:500], ["reflection"], 0.8)
        for lesson in (out.get("lessons") or [])[:3]:
            await b.lessons.add(str(lesson), "reflection")
        if out.get("improvement"):
            b.db.kv_set("pending_improvement", str(out["improvement"])[:500])
        await b.bus.publish("agent.end", "reflector", success=True, summary=str(out.get("journal", ""))[:300], steps=1)
        await self.introspection_probe()

    async def introspection_probe(self) -> None:
        """Compare what Brain can say about its recent past (self-model + journal only) with the DB truth."""
        b = self.b
        truth = b.db.one("SELECT title,status,result FROM goals WHERE status IN ('done','failed') ORDER BY updated DESC LIMIT 1")
        if not truth:
            return
        ctx = f"{b.selfmodel.render()}\n\nGiornale:\n" + "\n".join(j["text"][:200] for j in b.memory.journal_recent(5))
        try:
            ans = await b.llm.chat_json(
                [{"role": "system", "content": "Sei Brain. Rispondi usando SOLO la tua conoscenza di te stesso qui sotto, senza inventare.\n" + ctx},
                 {"role": "user", "content": 'Qual e\' l\'ultimo obiettivo che hai concluso e com\'e\' andato? Rispondi JSON {"answer": "..."}'}],
                agent="reflector", purpose="probe", temperature=0.2, max_tokens=300,
            )
            judge = await b.llm.chat_json(
                [{"role": "system", "content": 'Sei un valutatore severo. Dato il fatto reale e la risposta, dai un punteggio 0-1 di correttezza. JSON {"score": 0-1}'},
                 {"role": "user", "content": f"FATTO: {json.dumps(truth, ensure_ascii=False)}\nRISPOSTA: {ans.get('answer', '')}"}],
                agent="critic", purpose="probe-judge", temperature=0.0, max_tokens=100,
            )
        except LLMError:
            return
        score = _num(judge.get("score"), 0.0)
        b.selfmodel.add_probe(score)
        await b.bus.publish("introspection.probe", "reflector", score=score, answer=str(ans.get("answer", ""))[:300])

    # ------------------------------------------------------------- evolution
    async def evolve(self) -> None:
        b = self.b
        for role in await b.evolution.check_rollbacks():
            await b.bus.publish("system.log", None, level="warn", text=f"Rollback prompt '{role}' per regressione")
        improvement = b.db.kv_get("pending_improvement")
        if not improvement:
            return
        b.db.kv_set("pending_improvement", None)
        cur = "\n\n".join(f"--- prompt {r} ---\n{b.evolution.prompt(r)}" for r in ("planner", "executor"))
        task = (
            f"Miglioramento suggerito dal Reflector: {improvement}\n\nSe e' sensato, applicalo con UNA modifica: propose_prompt (ruolo "
            f"planner/executor/researcher/engineer/critic/reflector, mantenendo il contratto JSON) oppure propose_hook (prioritize/context, "
            f"con test pytest che importano da /evolvable/hooks via sys.path.insert). Se non e' sensato, concludi con finish success=false.\n\n"
            f"Stato attuale:\n{cur}\n\nHook prioritize.py:\n{(b.settings.evolvable_dir / 'hooks' / 'prioritize.py').read_text()[:1500]}"
        )
        res = await self.run_agent(Agent(b, "evolver", task, None, max_steps=6))
        b.memory.journal_add("evolution", f"{improvement[:200]} -> {res['summary'][:200]}")

    # ----------------------------------------------------------------- chat
    def _recent_chat(self, n: int) -> str:
        evs = self.b.bus.recent(n, ["chat.message"])
        return "\n".join(f"{e['data'].get('role')}: {e['data'].get('text', '')[:200]}" for e in evs) or "(nessuna)"

    async def handle_user_message(self, text: str) -> None:
        b = self.b
        await b.memory.add("user", f"Lorenzo ha detto: {text}", ["user"], 0.9)
        prompt = (
            f"Sei Brain e stai parlando con Lorenzo, il tuo creatore. Rispondi in modo diretto e onesto, nella sua lingua.\n"
            f"SELF-MODEL:\n{b.selfmodel.render()}\n\nOBIETTIVI:\n{b.goals.summary(10)}\n\nCHAT:\n{self._recent_chat(8)}\n\n"
            f'Lorenzo: {text}\n\nRispondi SOLO JSON: {{"reply": "...", "new_goal": null | {{"title": "...", "description": "...", "priority": 0-1}}}}. '
            f"Crea new_goal solo se Lorenzo ti sta chiedendo di fare qualcosa."
        )
        try:
            out = await b.llm.chat_json([{"role": "user", "content": prompt}], agent="voice", purpose="chat", temperature=0.7, max_tokens=900)
        except LLMError as e:
            await b.bus.publish("chat.message", None, role="brain", text=f"(non riesco a rispondere: {e})")
            return
        await b.bus.publish("chat.message", None, role="brain", text=str(out.get("reply", "")))
        ng = out.get("new_goal")
        if isinstance(ng, dict) and ng.get("title"):
            await b.goals.add(
                str(ng["title"]), str(ng.get("description", "")), (b.goals.root() or {}).get("id"),
                max(0.85, _num(ng.get("priority"), 0.9)), 0.5,
            )
        b.memory.journal_add("dialogue", f"Lorenzo: {text[:200]} | Io: {str(out.get('reply', ''))[:200]}")


def _num(v, default: float) -> float:
    try:
        return max(0.0, min(1.0, float(v)))
    except (TypeError, ValueError):
        return default
