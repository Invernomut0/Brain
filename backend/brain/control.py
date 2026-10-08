"""Run-state control: pause / resume / stop / kill and budgets."""
from __future__ import annotations

import asyncio

from .bus import EventBus
from .db import Database


class Halt(Exception):
    """Raised inside agent loops when the system is stopped or killed."""


class Control:
    def __init__(self, db: Database, bus: EventBus, max_cycles: int, max_tokens: int):
        self.db, self.bus = db, bus
        self.state = "idle"  # idle | running | paused | stopped | killed
        self.cycle = int(db.kv_get("cycle", 0))
        self.max_cycles = max_cycles
        self.max_tokens = max_tokens
        self._resume = asyncio.Event()
        self._resume.set()

    def snapshot(self) -> dict:
        return {"state": self.state, "cycle": self.cycle, "max_cycles": self.max_cycles, "max_tokens": self.max_tokens}

    async def set_state(self, state: str) -> None:
        self.state = state
        if state == "paused":
            self._resume.clear()
        else:
            self._resume.set()
        await self.bus.publish("control.state", None, **self.snapshot())

    async def set_budget(self, max_cycles: int | None, max_tokens: int | None) -> None:
        if max_cycles is not None:
            self.max_cycles = max(0, int(max_cycles))
        if max_tokens is not None:
            self.max_tokens = max(0, int(max_tokens))
        await self.bus.publish("control.state", None, **self.snapshot())

    def next_cycle(self) -> int:
        self.cycle += 1
        self.db.kv_set("cycle", self.cycle)
        return self.cycle

    def budget_exhausted(self, tokens_used: int) -> str | None:
        if self.max_cycles and self.cycle >= self.max_cycles:
            return "cycle budget exhausted"
        if self.max_tokens and tokens_used >= self.max_tokens:
            return "token budget exhausted"
        return None

    async def gate(self) -> None:
        """Checkpoint called between agent steps: blocks while paused, raises on stop/kill."""
        if self.state in ("stopped", "killed"):
            raise Halt(self.state)
        await self._resume.wait()
        if self.state in ("stopped", "killed"):
            raise Halt(self.state)
