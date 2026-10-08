"""Composition root: wires every subsystem together."""
from __future__ import annotations

import asyncio
import time

import psutil

from .bus import EventBus
from .config import ROOT_GOAL, Settings
from .control import Control
from .db import Database
from .evolution import Evolution
from .goals import GoalStore
from .llm import LLMClient
from .memory import Memory
from .orchestrator import Orchestrator
from .sandbox import Sandbox
from .selfmodel import SelfModel
from .tools import ToolRegistry


class Brain:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings()
        self.settings.ensure_dirs()
        self.db = Database(self.settings.data_dir / "brain.db")
        self.bus = EventBus(self.db)
        self.llm = LLMClient(self.settings, self.bus)
        self.sandbox = Sandbox(self.settings)
        self.memory = Memory(self.db, self.llm, self.bus)
        self.goals = GoalStore(self.db, self.bus)
        self.selfmodel = SelfModel(self.db, self.bus)
        self.evolution = Evolution(self.settings, self.db, self.bus, self.sandbox)
        self.control = Control(self.db, self.bus, self.settings.max_cycles, self.settings.max_tokens)
        self.tools = ToolRegistry(self)
        self.orchestrator = Orchestrator(self)
        self.started = time.time()
        self._metrics_task: asyncio.Task | None = None
        self._health: dict = {}

    async def startup(self) -> None:
        await self.evolution.seed()
        if not self.goals.root():
            await self.goals.add("Evolvere in intelligenza autonoma e raggiungere l'autocoscienza", ROOT_GOAL, None, 1.0, None, status="active")
        self._metrics_task = asyncio.create_task(self._metrics_loop())
        if self.settings.autostart:
            await self.start()

    async def shutdown(self) -> None:
        await self.kill()
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
        await self.bus.publish("system.log", None, level="warn", text="KILL: agenti e container terminati")

    # -------------------------------------------------------------- state
    def state_brief(self) -> dict:
        return {
            "cycle": self.control.cycle, "metrics": self.selfmodel.metrics(),
            "tools": list(self.tools.custom()), "goals": len(self.goals.all()),
        }

    def snapshot(self) -> dict:
        return {
            "control": self.control.snapshot(),
            "goals": self.goals.all(),
            "agents": [
                {"id": a.id, "role": a.role, "parent": a.parent.id if a.parent else None, "goal_id": a.goal_id, "steps": a.steps}
                for a in self.orchestrator.live.values()
            ],
            "tools": [
                {"name": t.name, "description": t.description, "custom": t.custom} for t in self.tools.all().values()
            ],
            "custom_tools": self.db.query("SELECT name,description,status,calls,failures FROM tools"),
            "selfmodel": self.selfmodel.get(),
            "metrics": self.selfmodel.metrics(),
            "journal": self.memory.journal_recent(30),
            "evolutions": self.evolution.history(20),
            "events": self.bus.recent(150),
            "health": self._health,
            "chat": [e["data"] | {"ts": e["ts"]} for e in self.bus.recent(60, ["chat.message"])],
        }

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
                tps=round(self.llm.last_tps, 1), tokens=self.llm.total_tokens, llm_busy=self.llm.busy,
                calls=self.llm.calls, live_agents=len(self.orchestrator.live), uptime=round(time.time() - self.started),
                health=self._health, control=self.control.snapshot(), metrics=self.selfmodel.metrics(),
            )
            await asyncio.sleep(2)
