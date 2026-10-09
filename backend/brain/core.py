"""Composition root: wires every subsystem together."""
from __future__ import annotations

import asyncio
import json
import shutil
import time

import psutil

from .bus import EventBus
from .config import ROOT_GOAL, Settings
from .control import Control
from .db import Database
from .evolution import Evolution
from .goals import GoalStore
from .lessons import Lessons
from .llm import LLMClient, LLMError
from .memory import Memory
from .orchestrator import Orchestrator
from .projects import Projects
from .sandbox import Sandbox
from .selfmodel import DEFAULT_MODEL, SelfModel
from .status import StatusReport
from .wiki import Wiki
from .tools import ToolRegistry

OWNER_KEY = "owner_name"


class Brain:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings()
        self.settings.ensure_dirs()
        self.db = Database(self.settings.data_dir / "brain.db")
        self.bus = EventBus(self.db)
        self.llm = LLMClient(self.settings, self.bus)
        self.sandbox = Sandbox(self.settings)
        self.memory = Memory(self.db, self.llm, self.bus)
        self.lessons = Lessons(self.db, self.bus)
        self.goals = GoalStore(self.db, self.bus)
        self.selfmodel = SelfModel(self.db, self.bus)
        self.evolution = Evolution(self.settings, self.db, self.bus, self.sandbox)
        self.control = Control(self.db, self.bus, self.settings.max_cycles, self.settings.max_tokens)
        self.tools = ToolRegistry(self)
        self.orchestrator = Orchestrator(self)
        self.status = StatusReport(self)
        self.wiki = Wiki(self)
        self.projects = Projects(self)
        self.started = time.time()
        self._metrics_task: asyncio.Task | None = None
        self._health: dict = {}

    async def startup(self) -> None:
        await self._bootstrap()
        await self.wiki.init()
        self.wiki.start()
        self._metrics_task = asyncio.create_task(self._metrics_loop())
        asyncio.create_task(self._warm_sandbox())
        if self.settings.autostart:
            await self.start()

    async def _bootstrap(self) -> None:
        await self.evolution.seed()
        # Goals left 'active' by a crash/kill would never be picked up again.
        self.db.execute("UPDATE goals SET status='pending' WHERE status='active' AND parent_id IS NOT NULL")
        if not self.goals.root():
            await self.goals.add("Evolve into an autonomous intelligence and reach self-awareness", ROOT_GOAL, None, 1.0, None, status="active")

    async def set_main_goal(self, text: str, archive_pending: bool = True) -> dict:
        """Change what Brain is ultimately trying to achieve; the planner picks it up on its next run."""
        text = text.strip()
        if not 10 <= len(text) <= 2000:
            raise ValueError("main goal must be 10-2000 characters")
        root = await self.goals.set_main(text, archive_pending)
        await self._realign_selfmodel(text)
        self.memory.journal_add("goal", f"{self.owner} changed the main goal: {text[:300]}")
        await self.memory.add("user", f"New main goal decided by {self.owner}: {text}", ["user", "goal"], 1.0)
        await self.bus.publish("goal.main_changed", None, text=text[:300], cancelled=root["cancelled"])
        return root

    async def _realign_selfmodel(self, goal: str) -> None:
        """Purpose, open questions and hypotheses were written for the old goal: rewrite them for the new one."""
        old = self.selfmodel.get()
        patch: dict = {"purpose": goal[:300], "open_questions": [], "hypotheses": []}
        try:
            out = await asyncio.wait_for(self.llm.chat_json(
                [{"role": "system", "content": (
                    "You maintain Brain's self-model. Its main goal just changed, so the purpose and open questions written for the "
                    "old goal are obsolete. Reply ONLY with JSON {\"purpose\": \"...\", \"open_questions\": [\"...\"]}. "
                    "purpose: first person, max 300 characters, faithful to the new goal, nothing invented. "
                    "open_questions: 3 to 5 concrete questions Brain must answer to achieve the new goal.")},
                 {"role": "user", "content": (
                    f"OLD PURPOSE: {old['purpose']}\nOLD OPEN QUESTIONS: {'; '.join(map(str, old['open_questions']))}\n\n"
                    f"NEW MAIN GOAL (chosen by {self.owner}): {goal}")}],
                agent="reflector", purpose="realign self-model", temperature=0.3, max_tokens=500,
            ), 90)
            purpose = str(out.get("purpose") or "").strip()
            questions = [str(q).strip()[:200] for q in (out.get("open_questions") or []) if str(q).strip()][:5]
            if purpose:
                patch["purpose"] = purpose[:300]
            if questions:
                patch["open_questions"] = questions
        except (LLMError, asyncio.TimeoutError):
            pass  # keep the deterministic fallback: the new goal as purpose, stale questions dropped
        await self.selfmodel.update(patch)

    # ---------------------------------------------------------- owner
    @property
    def owner_name(self) -> str:
        """Who Brain works for: asked in the dashboard, stored with the project (empty until given)."""
        return self.db.kv_get(OWNER_KEY) or ""

    @property
    def owner(self) -> str:
        """The owner as written inside prompts and messages."""
        return self.owner_name or "the owner"

    async def set_owner(self, name: str) -> str:
        name = " ".join(str(name).split())[:60]
        if not name:
            raise ValueError("owner name is required")
        old = self.owner_name
        self.db.kv_set(OWNER_KEY, name)
        about = self.selfmodel.get()["about_user"]
        if old and old in about:
            about = about.replace(old, name)
        elif about == DEFAULT_MODEL["about_user"]:
            about = f"{name} is my creator and the only human I talk to; they can read me from the chat."
        await self.selfmodel.update({"about_user": about})
        await self.bus.publish("owner.changed", None, name=name)
        return name

    async def reset(self) -> None:
        """Back to a brand-new installation: database, sandbox workspace (incl. tools), prompts, hooks, counters."""
        await self._halt_everything()
        self.db.wipe()
        ws = self.settings.workspace_dir
        for child in ws.iterdir():
            shutil.rmtree(child) if child.is_dir() else child.unlink()
        (ws / "tools").mkdir(parents=True, exist_ok=True)
        await self.sandbox.close()  # fresh container: pip installs and processes are gone too
        await self.evolution.reset()
        self.selfmodel.reset()
        await self._fresh_runtime(0)
        await self._bootstrap()
        await self.wiki.reset()
        await self.bus.publish("system.reset", None)
        if self.settings.autostart:
            await self.start()

    async def _halt_everything(self) -> None:
        """Kill agents/containers and drop in-flight background work and pending questions."""
        await self.kill()
        o = self.orchestrator
        for t in list(o._bg):
            t.cancel()
        o.questions.clear()
        o.last_question = None

    async def _fresh_runtime(self, cycle: int) -> None:
        """Zero the in-memory counters and go back to idle at the given cycle."""
        self.llm.total_tokens = self.llm.calls = 0
        self.llm.last_tps = 0.0
        self.llm.streams.clear()
        self.control.cycle = cycle
        await self.control.set_state("idle")

    async def shutdown(self) -> None:
        await self.kill()
        await self.wiki.stop()
        await self.sandbox.close()
        if self._metrics_task:
            self._metrics_task.cancel()
        await self.llm.close()

    # ---------------------------------------------------------- control API
    async def start(self) -> None:
        o = self.orchestrator
        if o.task and not o.task.done():
            if self.control.state == "paused":
                await self.control.set_state("running")
            return
        self.control.cycle = 0 if self.control.state in ("killed", "stopped") else self.control.cycle
        o.task = asyncio.create_task(o.main_loop())
        await self.control.set_state("running")

    async def pause(self) -> None:
        if self.control.state == "running":
            await self.control.set_state("paused")

    async def resume(self) -> None:
        if self.control.state == "paused":
            await self.control.set_state("running")

    async def stop(self) -> None:
        """Graceful: agents halt at their next checkpoint."""
        await self.control.set_state("stopped")

    async def kill(self) -> None:
        """Hard: cancel every task and kill all sandbox containers."""
        await self.control.set_state("killed")
        t = self.orchestrator.task
        if t and not t.done():
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        await self.sandbox.kill_all()
        self.orchestrator.live.clear()
        await self.bus.publish("system.log", None, level="warn", text="KILL: agents and containers terminated")

    # -------------------------------------------------------------- state
    def state_brief(self) -> dict:
        return {
            "cycle": self.control.cycle, "metrics": self.selfmodel.metrics(),
            "tools": list(self.tools.custom()), "goals": len(self.goals.all()),
        }

    def _agents_snapshot(self) -> list[dict]:
        """Rebuild each agent's current state from this run's events so a reloaded dashboard is accurate."""
        rows = self.db.query(
            "SELECT type,agent,data,ts FROM events WHERE ts>=? AND type IN "
            "('agent.spawn','agent.state','agent.thought','agent.end') ORDER BY seq DESC LIMIT 600",
            (self.started,),
        )
        agents: dict[str, dict] = {}
        for r in reversed(rows):
            d, a = json.loads(r["data"]), r["agent"]
            if r["type"] == "agent.spawn":
                agents[a] = {
                    "id": a, "role": d.get("role", "executor"), "parent": d.get("parent"), "goal_id": d.get("goal_id"),
                    "task": d.get("task", ""), "state": "idle", "detail": "", "thought": "", "action": "", "steps": 0,
                    "born": r["ts"], "ended": None, "success": None, "summary": "",
                }
            elif a in agents:
                x = agents[a]
                if r["type"] == "agent.state":
                    x["state"], x["detail"] = d.get("state", x["state"]), d.get("detail", "")
                elif r["type"] == "agent.thought":
                    x["thought"], x["action"], x["steps"] = d.get("thought", ""), d.get("action", ""), x["steps"] + 1
                else:
                    x.update(ended=r["ts"], success=d.get("success"), summary=d.get("summary", ""),
                             state="done" if d.get("success") else "failed")
        cutoff = time.time() - 30
        return [a for a in agents.values() if a["ended"] is None or a["ended"] > cutoff]

    def snapshot(self) -> dict:
        return {
            "project": self.projects.current(),
            "owner": self.owner_name,
            "control": self.control.snapshot(),
            "goals": self.goals.all(),
            "agents": self._agents_snapshot(),
            "streams": dict(self.llm.streams),
            "tools": [
                {"name": t.name, "description": t.description, "custom": t.custom} for t in self.tools.all().values()
            ],
            "custom_tools": self.db.query("SELECT name,description,status,calls,failures FROM tools"),
            "selfmodel": self.selfmodel.get(),
            "metrics": self.selfmodel.metrics(),
            "journal": self.memory.journal_recent(30),
            "lessons": sorted(self.lessons.all(), key=lambda i: (i["count"], i["ts"]), reverse=True),
            "evolutions": self.evolution.history(20),
            "events": self.bus.recent(150),
            "health": self._health,
            "chat": [e["data"] | {"ts": e["ts"]} for e in self.bus.recent(60, ["chat.message"])],
        }

    async def _warm_sandbox(self) -> None:
        try:
            await self.sandbox.ensure_container()
        except Exception as e:  # noqa: BLE001 - surfaced via the health chip; retried on first use
            await self.bus.publish("system.log", None, level="warn", text=f"Sandbox not ready: {e}")

    async def _metrics_loop(self) -> None:
        proc = psutil.Process()
        psutil.cpu_percent(None)
        n = 0
        while True:
            if n % 5 == 0:  # external services checked every ~10s
                self._health = {
                    "llm": await self.llm.health(),
                    "sandbox": await self.sandbox.status(),
                }
            n += 1
            await self.bus.publish(
                "system.metrics", None,
                cpu=psutil.cpu_percent(None), mem=psutil.virtual_memory().percent,
                proc_mb=round(proc.memory_info().rss / 1e6),
                tps=round(self.llm.current_tps, 1), tokens=self.llm.total_tokens, llm_busy=self.llm.busy, llm_queued=self.llm.queued,
                calls=self.llm.calls, live_agents=len(self.orchestrator.live), uptime=round(time.time() - self.started),
                health=self._health, control=self.control.snapshot(), metrics=self.selfmodel.metrics(),
            )
            await asyncio.sleep(2)
