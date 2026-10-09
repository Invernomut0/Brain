"""Memorable agent names (instead of ids like critic-4) in a few selectable styles, persisted per agent.

An agent keeps its technical id (events, containers, goals); the name is only what people read. Names are unique
(case-insensitive), stable (the same agent id always gets the same name) and can be rolled again or chosen by hand.
"""
from __future__ import annotations

import random
import re
import sqlite3
import time

from .db import Database
from .names_data import DICTIONARY, PATTERNS, ROLE_FLAVORS

TOKEN_RE = re.compile(r"\{(\w+)\}")
KV_STYLE = "naming_style"
OFF, ALL, CUSTOM = "off", "all", "custom"
MAX_NAME = 32  # typed by hand
MAX_GENERATED = 26  # keeps generated names readable in lists and graphs


class AgentNameError(ValueError):
    """The requested name is empty, too long, or already used by another agent."""


def clean(name: str) -> str:
    name = " ".join(str(name or "").split())
    if not name or len(name) > MAX_NAME:
        raise AgentNameError(f"a name of 1-{MAX_NAME} characters is required")
    if not name.isprintable():
        raise AgentNameError("the name contains invalid characters")
    return name


class AgentNames:
    def __init__(self, db: Database, seed: int | None = None):
        self.db = db
        self.rng = random.Random(seed) if seed is not None else random.SystemRandom()

    # ------------------------------------------------------------------ style
    @staticmethod
    def styles() -> list[str]:
        return [OFF, ALL, *PATTERNS]

    def style(self) -> str:
        s = self.db.kv_get(KV_STYLE, ALL)
        return s if s in self.styles() else ALL

    def set_style(self, style: str) -> str:
        if style not in self.styles():
            raise AgentNameError(f"unknown style '{style}'. Available: {', '.join(self.styles())}")
        self.db.kv_set(KV_STYLE, style)
        return style

    # ------------------------------------------------------------- generation
    def _expand(self, pattern: str) -> str:
        return TOKEN_RE.sub(lambda m: self.rng.choice(DICTIONARY[m.group(1)]), pattern)

    def _styles_for(self, style: str, role: str | None) -> list[str]:
        if style == ALL:
            return ROLE_FLAVORS.get((role or "").lower(), list(PATTERNS))
        return [style]

    def _generate(self, role: str | None, style: str) -> tuple[str, str]:
        """A name nobody uses yet, with the style it came from."""
        styles = self._styles_for(style, role)
        for _ in range(2000):
            used = self.rng.choice(styles)
            name = self._expand(self.rng.choice(PATTERNS[used]))
            if len(name) <= MAX_GENERATED and not self.db.one("SELECT 1 FROM agent_names WHERE name=?", (name,)):
                return name, used
        raise RuntimeError("unable to generate a unique agent name after 2000 attempts")

    # ----------------------------------------------------------------- access
    def get(self, agent_id: str) -> dict | None:
        return self.db.one("SELECT agent_id,name,role,style,created FROM agent_names WHERE agent_id=?", (agent_id,))

    def all(self) -> dict[str, str]:
        return {r["agent_id"]: r["name"] for r in self.db.query("SELECT agent_id,name FROM agent_names")}

    def assign(self, agent_id: str, role: str | None = None) -> str | None:
        """Name of the agent for the current style, creating it on first sight; None when naming is off."""
        if self.style() == OFF:
            return None
        known = self.get(agent_id)
        if known:
            return known["name"]
        return self._store(agent_id, role)["name"]

    def _store(self, agent_id: str, role: str | None) -> dict:
        for _ in range(20):  # a name taken between generation and insert is simply regenerated
            name, used = self._generate(role, self.style() if self.style() != OFF else ALL)
            try:
                self.db.execute(
                    "INSERT INTO agent_names(agent_id,name,role,style,created) VALUES(?,?,?,?,?)",
                    (agent_id, name, role, used, time.time()),
                )
                return {"agent_id": agent_id, "name": name, "role": role, "style": used}
            except sqlite3.IntegrityError:
                if self.get(agent_id):
                    return self.get(agent_id)  # type: ignore[return-value]
        raise RuntimeError("unable to store an agent name")

    def reroll(self, agent_id: str, role: str | None = None) -> str:
        """Replace the agent's name with a freshly generated one."""
        old = self.get(agent_id)
        role = role or (old or {}).get("role")
        name, used = self._generate(role, self.style() if self.style() != OFF else ALL)
        self._write(agent_id, name, used, role)
        return name

    def rename(self, agent_id: str, name: str, role: str | None = None) -> str:
        """Use a name chosen by hand (unique, case-insensitive)."""
        name = clean(name)
        clash = self.db.one("SELECT agent_id FROM agent_names WHERE name=? COLLATE NOCASE", (name,))
        if clash and clash["agent_id"] != agent_id:
            raise AgentNameError(f"'{name}' is already used by another agent")
        self._write(agent_id, name, CUSTOM, role or (self.get(agent_id) or {}).get("role"))
        return name

    def _write(self, agent_id: str, name: str, style: str, role: str | None) -> None:
        self.db.execute(
            "INSERT INTO agent_names(agent_id,name,role,style,created) VALUES(?,?,?,?,?) "
            "ON CONFLICT(agent_id) DO UPDATE SET name=excluded.name, style=excluded.style",
            (agent_id, name, role, style, time.time()),
        )
