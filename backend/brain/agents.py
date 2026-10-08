"""ReAct-style agent: think -> act (tool) -> observe, until `finish`."""
from __future__ import annotations

import json
import time
import uuid
from typing import TYPE_CHECKING, Any

from .control import Halt
from .llm import LLMError
from .prompts import PROTOCOL
from .tools import ToolContext, _clip

if TYPE_CHECKING:
    from .core import Brain

ROLE_TOOLS: dict[str, list[str] | None] = {
    "executor": None,  # all tools
    "researcher": ["web_search", "web_fetch", "http_request", "remember", "recall", "read_file", "write_file", "list_files", "python_exec"],
    "engineer": ["python_exec", "shell_exec", "read_file", "write_file", "list_files", "create_tool", "web_search", "web_fetch", "recall", "remember"],
    "evolver": ["propose_prompt", "propose_hook"],
}


class Agent:
    def __init__(
        self, brain: "Brain", role: str, task: str, goal_id: int | None = None,
        parent: "Agent | None" = None, system_prompt: str = "", max_steps: int | None = None,
    ):
        self.brain = brain
        self.role = role
        self.id = f"{role[:4]}-{uuid.uuid4().hex[:4]}"
        self.task = task
        self.goal_id = goal_id
        self.parent = parent
        self.depth = parent.depth + 1 if parent else 0
        self.custom_prompt = system_prompt
        self.max_steps = max_steps or brain.settings.agent_max_steps
        self.inbox: list[tuple[str, str]] = []
        self.trace: list[dict[str, Any]] = []
        self.steps = 0

    # -- prompt ------------------------------------------------------------
    def _system(self) -> str:
        names = ROLE_TOOLS.get(self.role)
        base = self.custom_prompt or self.brain.evolution.prompt(self.role)
        return (
            f"{base}\n\nTool disponibili:\n{self.brain.tools.describe(names)}\n\n{PROTOCOL}\n\n"
            f"Il tuo id: {self.id}. Ruolo: {self.role}.\nSelf-model:\n{self.brain.selfmodel.render()}"
        )

    async def _state(self, state: str, detail: str = "") -> None:
        await self.brain.bus.publish("agent.state", self.id, state=state, detail=detail[:200])

    # -- main loop --------------------------------------------------------------
    async def run(self) -> dict:
        b = self.brain
        b.db.execute(
            "INSERT INTO agents(id,role,parent,goal_id,status,started) VALUES(?,?,?,?,?,?)",
            (self.id, self.role, self.parent.id if self.parent else None, self.goal_id, "running", time.time()),
        )
        await b.bus.publish(
            "agent.spawn", self.id, role=self.role, parent=self.parent.id if self.parent else None,
            goal_id=self.goal_id, task=self.task[:300],
        )
        names = ROLE_TOOLS.get(self.role)
        allowed = b.tools.all(names)
        msgs: list[dict] = [{"role": "system", "content": self._system()}, {"role": "user", "content": f"COMPITO:\n{self.task}"}]
        success, summary = False, "nessun risultato"
        try:
            for _ in range(self.max_steps):
                await b.control.gate()
                while self.inbox:
                    frm, txt = self.inbox.pop(0)
                    msgs.append({"role": "user", "content": f"[messaggio da {frm}] {txt}"})
                self.steps += 1
                await self._state("thinking")
                step = await b.llm.chat_json(msgs, agent=self.id, purpose=f"{self.role} step {self.steps}", temperature=0.6, max_tokens=1800)
                action, args = str(step.get("action", "")), step.get("args") or {}
                if not isinstance(args, dict):
                    args = {}
                await b.bus.publish("agent.thought", self.id, thought=str(step.get("thought", ""))[:500], action=action)
                msgs.append({"role": "assistant", "content": json.dumps(step, ensure_ascii=False)[:2500]})
                if action == "finish":
                    success = bool(args.get("success", False))
                    summary = str(args.get("summary", ""))[:2000]
                    break
                obs = await self._act(action, args, allowed)
                msgs.append({"role": "user", "content": f"OSSERVAZIONE ({action}):\n{obs}"})
                msgs = self._trim(msgs)
            else:
                summary = "limite di passi raggiunto senza finish"
        except Halt:
            summary = "interrotto"
            await self._finish(False, summary)
            raise
        except LLMError as e:
            summary = f"errore LLM: {e}"
        except Exception as e:  # noqa: BLE001
            summary = f"errore: {type(e).__name__}: {e}"
        await self._finish(success, summary)
        return {"success": success, "summary": summary, "trace": self.trace[-12:], "steps": self.steps}

    async def _act(self, action: str, args: dict, allowed: dict) -> str:
        b = self.brain
        if action not in allowed:
            return f"ERRORE: tool '{action}' inesistente. Disponibili: {', '.join(allowed)}"
        await self._state("acting", action)
        await b.bus.publish("tool.call", self.id, tool=action, args=_clip(args, 300))
        try:
            res = await b.tools.call(ToolContext(b, self), action, args)
            ok, obs = True, _clip(res, 2500)
        except Exception as e:  # noqa: BLE001
            ok, obs = False, f"ERRORE {type(e).__name__}: {str(e)[:900]}"
        self.trace.append({"tool": action, "ok": ok, "obs": obs[:300]})
        await b.bus.publish("tool.result", self.id, tool=action, ok=ok, preview=obs[:200])
        return obs

    @staticmethod
    def _trim(msgs: list[dict], keep: int = 10) -> list[dict]:
        return msgs if len(msgs) <= keep + 2 else msgs[:2] + msgs[-keep:]

    async def _finish(self, success: bool, summary: str) -> None:
        b = self.brain
        b.db.execute(
            "UPDATE agents SET status=?, ended=?, steps=?, result=? WHERE id=?",
            ("done" if success else "failed", time.time(), self.steps, summary[:1000], self.id),
        )
        await b.bus.publish("agent.end", self.id, success=success, summary=summary[:500], steps=self.steps)
