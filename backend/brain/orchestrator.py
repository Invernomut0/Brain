"""The autonomous mind: plans goals, runs agents, critiques, reflects and evolves."""
from __future__ import annotations

import asyncio
import json
import time
from typing import TYPE_CHECKING

from .agents import Agent
from .config import ROOT_GOAL
from .control import Halt
from .llm import LLMError

if TYPE_CHECKING:
    from .core import Brain

MAX_LIVE_AGENTS = 12
MAX_DEPTH = 2
VALID_ROLES = {"executor", "researcher", "engineer"}


class Orchestrator:
    def __init__(self, brain: "Brain"):
        self.b = brain
        self.live: dict[str, Agent] = {}
        self.task: asyncio.Task | None = None
        self._bg: set[asyncio.Task] = set()
        self.questions: list[dict] = []  # agents currently waiting for Lorenzo's reply
        self.last_question: dict | None = None
        self.running: dict[int, asyncio.Task] = {}  # goal id -> task: goals executed in parallel
        self._plan_task: asyncio.Task | None = None
        self._maint: dict[str, asyncio.Task] = {}

    # ------------------------------------------------------------- agents
    def main_goal(self) -> str:
        """The current main goal text (editable by Lorenzo), falling back to the factory default."""
        return (self.b.goals.root() or {}).get("description") or ROOT_GOAL
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

    async def spawn_many(self, parent: Agent, specs: list) -> str:
        """Run several sub-agents concurrently and return all their results."""
        specs = [s for s in specs if isinstance(s, dict) and s.get("task")][:4]
        if not specs:
            return "ERRORE: passa tasks=[{role, task}, ...] (max 4)"
        results = await asyncio.gather(*(
            self.spawn_and_run(parent, str(s.get("role", "executor")), str(s["task"]), str(s.get("system_prompt", "")))
            for s in specs
        ))
        return "\n".join(results)

    # ----------------------------------------------------------- main loop
    async def main_loop(self) -> None:
        """Scheduler: keeps up to `max_parallel_agents` goals running concurrently while planning in the background."""
        b = self.b
        await b.control.set_state("running")
        await b.bus.publish("system.log", None, level="info", text=f"Brain avviato (fino a {b.settings.max_parallel_agents} obiettivi in parallelo)")
        try:
            while True:
                await b.control.gate()
                reason = b.control.budget_exhausted(b.llm.total_tokens)
                if reason:
                    await b.bus.publish("system.log", None, level="warn", text=f"Pausa automatica: {reason}")
                    await b.control.set_state("paused")
                    continue
                await self._fill_slots()
                waiting: set[asyncio.Task] = set(self.running.values())
                if self._plan_task and not self._plan_task.done():
                    waiting.add(self._plan_task)
                if not waiting:
                    await asyncio.sleep(1)
                    continue
                await asyncio.wait(waiting, timeout=2, return_when=asyncio.FIRST_COMPLETED)
                await self._reap()
        except Halt:
            await b.bus.publish("system.log", None, level="info", text=f"Fermato ({b.control.state})")
        finally:
            tasks = [*self.running.values(), *self._maint.values(), *([self._plan_task] if self._plan_task else [])]
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self.running.clear()

    async def _fill_slots(self) -> None:
        b = self.b
        while len(self.running) < max(1, b.settings.max_parallel_agents):
            pending = [g for g in b.goals.pending() if g["id"] not in self.running]
            if len(pending) < 2 and (self._plan_task is None or self._plan_task.done()):
                self._plan_task = asyncio.create_task(self._plan_safe())
            if not pending:
                return
            goal = await self._choose(pending)
            await b.bus.publish("cycle.start", None, goal_id=goal["id"], parallel=len(self.running) + 1)
            self.running[goal["id"]] = asyncio.create_task(self._run_goal(goal))

    async def _reap(self) -> None:
        """Collect finished tasks; a Halt propagates, other failures are logged without stopping the system."""
        b = self.b
        for gid, t in list(self.running.items()):
            if not t.done():
                continue
            self.running.pop(gid)
            exc = None if t.cancelled() else t.exception()
            if isinstance(exc, Halt):
                raise exc
            if exc:
                await b.bus.publish("system.log", None, level="error", text=f"Obiettivo #{gid}: {type(exc).__name__}: {str(exc)[:200]}")
                await asyncio.sleep(2 if not isinstance(exc, LLMError) else 5)

    async def _plan_safe(self) -> None:
        try:
            await self.plan()
        except LLMError as e:
            await self.b.bus.publish("system.log", None, level="error", text=f"Planner: LLM non disponibile: {e}")
            await asyncio.sleep(5)

    async def _run_goal(self, goal: dict) -> None:
        b = self.b
        try:
            await self.execute_goal(goal)
        except (Halt, asyncio.CancelledError):
            b.db.execute("UPDATE goals SET status='pending' WHERE id=? AND status='active'", (goal["id"],))
            raise
        n = b.control.next_cycle()
        await b.bus.publish("cycle.end", None, cycle=n, goal_id=goal["id"], metrics=b.selfmodel.metrics())
        if n % b.settings.reflect_every == 0:
            self._background(self.reflect)
        if n % b.settings.evolve_every == 0:
            self._background(self.evolve)

    def _background(self, fn) -> None:
        """Run reflection/evolution beside the goals, never two of the same kind at once."""
        key = fn.__name__
        if key in self._maint and not self._maint[key].done():
            return

        async def guarded():
            try:
                await fn()
            except (Halt, asyncio.CancelledError):
                raise
            except Exception as e:  # noqa: BLE001 - maintenance must never take the system down
                await self.b.bus.publish("system.log", None, level="error", text=f"{key}: {type(e).__name__}: {str(e)[:200]}")

        self._maint[key] = asyncio.create_task(guarded())

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
        memories = await b.memory.search(self.main_goal(), 3)
        hook_ctx = await b.evolution.call_hook("context", "build_context", b.state_brief()) or ""
        chat = self._recent_chat(6)
        prompt = (
            f"OBIETTIVO RADICE (scelto da Lorenzo, puo' cambiare; prevale su ogni altra indicazione): {self.main_goal()}\n\nSELF-MODEL:\n{b.selfmodel.render()}\n\n"
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
        started = time.time()
        await b.goals.set_status(goal["id"], "active")
        role = goal.get("role") or "executor"
        task = f"OBIETTIVO #{goal['id']}: {goal['title']}\n{goal['description']}\n\nObiettivo radice di Brain: {self.main_goal()[:200]}"
        agent = Agent(b, role, task, goal["id"])
        res = await self.run_agent(agent)
        verdict = await self.critique(goal, res, started)
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

    async def critique(self, goal: dict, res: dict, since: float = 0.0) -> dict:
        b = self.b
        cid = f"critic-{goal['id']}"
        await b.bus.publish("agent.spawn", cid, role="critic", parent=None, goal_id=goal["id"], task="valutazione")
        evidence = "\n".join(f"- {t['tool']} ok={t['ok']}: {t['obs'][:200]}" for t in res["trace"])
        replies = [e["data"]["text"][:300] for e in b.bus.recent(80, ["chat.message"]) if e["ts"] >= since and e["data"].get("role") == "user"]
        prompt = (
            f"OBIETTIVO: {goal['title']}\nCRITERI: {goal['description']}\n\nDICHIARAZIONE DELL'AGENTE (success={res['success']}): "
            f"{res['summary']}\n\nPROVE (tool usati):\n{evidence or '(nessuno)'}"
            + ("\n\nMESSAGGI DI LORENZO ARRIVATI DURANTE L'ESECUZIONE:\n" + "\n".join(f"- {r}" for r in replies) if replies else "")
        )
        try:
            v = await b.llm.chat_json(
                [{"role": "system", "content": b.evolution.prompt("critic")}, {"role": "user", "content": prompt}],
                agent=cid, purpose="critique", temperature=0.2, max_tokens=500,
            )
        except LLMError:
            v = {"verdict": "pass" if res["success"] else "fail", "score": 0.5, "feedback": "critic non disponibile"}
        await b.bus.publish("agent.end", cid, success=v.get("verdict") == "pass", summary=str(v.get("feedback", ""))[:300], steps=1)
        await b.bus.publish("goal.verdict", cid, goal_id=goal["id"], **{k: v.get(k) for k in ("verdict", "score", "feedback")})
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

    async def ask(self, agent: Agent, text: str, wait: int) -> str | None:
        """Send a question to Lorenzo and wait (bounded) for his reply; None if he does not answer in time."""
        b = self.b
        await b.bus.publish("chat.message", agent.id, role="brain", text=text, agent_role=agent.role)
        self.last_question = {"goal_id": agent.goal_id, "text": text, "ts": time.time(), "answered": False}
        if wait <= 0:
            return None
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        q = {"agent_id": agent.id, "fut": fut}
        self.questions.append(q)
        try:
            deadline = time.time() + wait
            while not fut.done() and time.time() < deadline:
                if b.control.state in ("stopped", "killed"):
                    break
                await asyncio.wait({fut}, timeout=1.0)
            return fut.result() if fut.done() else None
        finally:
            self.questions.remove(q)

    async def route_user_message(self, text: str) -> tuple[list[str], int | None]:
        """Deliver Lorenzo's message to waiting/live agents and reopen the goal he was asked about."""
        b = self.b
        facts: list[str] = []

        waiting = [q for q in self.questions if not q["fut"].done()]
        for q in waiting:
            q["fut"].set_result(text)
            facts.append(f"risposta consegnata all'agente {q['agent_id']} che la stava aspettando")
        delivered = {q["agent_id"] for q in waiting}
        for aid, ag in self.live.items():
            if aid not in delivered and ag.role != "evolver":
                ag.inbox.append(("Lorenzo", text))
                facts.append(f"messaggio inoltrato all'agente {aid} (lo leggera' al prossimo passo)")

        lq = self.last_question
        reopened = None
        if lq and not lq["answered"] and time.time() - lq["ts"] < 3600:
            lq["answered"] = True
            g = b.goals.get(lq["goal_id"]) if lq["goal_id"] else None
            if g and g["status"] in ("failed", "pending"):
                await b.goals.reopen(g["id"], f"Risposta di Lorenzo alla tua domanda '{lq['text'][:150]}': {text}")
                reopened = g["id"]
                facts.append(f"riaperto l'obiettivo #{g['id']} '{g['title'][:60]}' con la sua risposta allegata")
        return facts, reopened

    async def handle_user_message(self, text: str) -> None:
        """Route Lorenzo's message to whoever needs it, then answer honestly about what actually happened."""
        b = self.b
        facts, reopened = await self.route_user_message(text)
        asyncio.create_task(b.memory.add("user", f"Lorenzo ha detto: {text}", ["user"], 0.9))  # off the reply's critical path

        prompt = (
            "Sei Brain e stai parlando con Lorenzo, il tuo creatore. Rispondi in modo breve, diretto e ONESTO, nella sua lingua.\n"
            "REGOLA: non dire mai di aver eseguito, misurato, salvato o integrato qualcosa. Puoi solo riferire i FATTI qui sotto "
            "e dire cosa farai nei prossimi cicli (che e' una promessa, non un risultato).\n"
            f"FATTI REALMENTE ACCADUTI: {'; '.join(facts) or 'nessuna azione ancora eseguita'}\n\n"
            f"SELF-MODEL:\n{b.selfmodel.render()}\n\nOBIETTIVI:\n{b.goals.summary(10)}\n\nCHAT:\n{self._recent_chat(8)}\n\n"
            f'Lorenzo: {text}\n\nRispondi SOLO JSON: {{"reply": "...", "new_goal": null | {{"title": "...", "description": "...", "priority": 0-1}}}}. '
            "Crea new_goal solo se Lorenzo chiede qualcosa di nuovo e nessun obiettivo esistente lo copre."
        )
        try:
            out = await b.llm.chat_json([{"role": "user", "content": prompt}], agent="voice", purpose="chat", temperature=0.5, max_tokens=900, priority=0)
        except LLMError as e:
            await b.bus.publish("chat.message", None, role="brain", text=f"(non riesco a rispondere: {e})")
            return
        ng = out.get("new_goal")
        if isinstance(ng, dict) and ng.get("title") and not reopened:
            gid = await b.goals.add(
                str(ng["title"]), str(ng.get("description", "")), (b.goals.root() or {}).get("id"),
                max(0.85, _num(ng.get("priority"), 0.9)), 0.5,
            )
            facts.append(f"creato il nuovo obiettivo #{gid}")
        note = f"\n\n[azioni reali: {'; '.join(facts)}]" if facts else ""
        await b.bus.publish("chat.message", None, role="brain", text=str(out.get("reply", "")) + note)
        b.memory.journal_add("dialogue", f"Lorenzo: {text[:200]} | Io: {str(out.get('reply', ''))[:200]}")


def _num(v, default: float) -> float:
    try:
        return max(0.0, min(1.0, float(v)))
    except (TypeError, ValueError):
        return default
