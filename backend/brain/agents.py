"""ReAct-style agent: think -> act (tool) -> observe, until `finish`."""
from __future__ import annotations

import difflib
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
    "researcher": ["web_search", "web_fetch", "http_request", "remember", "recall", "wiki_search", "wiki_read", "wiki_note", "read_file", "write_file", "list_files", "python_exec"],
    "engineer": ["python_exec", "shell_exec", "read_file", "write_file", "list_files", "create_tool", "web_search", "web_fetch", "recall", "remember", "wiki_search", "wiki_read"],
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
        self._fails: dict[str, tuple[str, str]] = {}

    # -- prompt ------------------------------------------------------------
    def _system(self) -> str:
        names = ROLE_TOOLS.get(self.role)
        base = self.custom_prompt or self.brain.evolution.prompt(self.role)
        lessons = self.brain.lessons.render(8)
        learned = f"LESSONS LEARNED from past errors (do not repeat them):\n{lessons}\n\n" if lessons else ""
        return (
            f"{base}\n\nAvailable tools:\n{self.brain.tools.describe(names)}\n\n{PROTOCOL}\n\n{learned}"
            f"Your id: {self.id}. Role: {self.role}.\nSelf-model:\n{self.brain.selfmodel.render()}"
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
        wiki_ctx = await b.wiki.context(self.task, 3) if "wiki_search" in allowed else ""
        task_msg = f"TASK:\n{self.task}" + (f"\n\nKNOWLEDGE ALREADY IN THE WIKI (wiki_read for details, do not redo what is already known):\n{wiki_ctx}" if wiki_ctx else "")
        msgs: list[dict] = [{"role": "system", "content": self._system()}, {"role": "user", "content": task_msg}]
        success, summary = False, "no result"
        format_errors = 0
        try:
            for _ in range(self.max_steps):
                await b.control.gate()
                while self.inbox:
                    frm, txt = self.inbox.pop(0)
                    msgs.append({"role": "user", "content": f"[message from {frm}] {txt}"})
                self.steps += 1
                await self._state("thinking")
                try:
                    step = await b.llm.chat_json(msgs, agent=self.id, purpose=f"{self.role} step {self.steps}", temperature=0.6, max_tokens=1800, retries=1)
                except LLMError as e:
                    if "valid JSON" not in str(e) or format_errors >= 2:
                        raise
                    # A malformed reply is not fatal: remember the lesson and resample the same step.
                    format_errors += 1
                    await b.lessons.add(
                        "Always reply with ONE single JSON object {thought, action, args}; multi-line code inside strings must use \\n escapes, never literal line breaks.",
                        "format",
                    )
                    continue
                format_errors = 0
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
                msgs.append({"role": "user", "content": f"OBSERVATION ({action}):\n{obs}"})
                msgs = self._trim(msgs)
            else:
                summary = "step limit reached without finish"
        except Halt:
            summary = "interrupted"
            await self._finish(False, summary)
            raise
        except LLMError as e:
            summary = f"LLM error: {e}"
        except Exception as e:  # noqa: BLE001
            summary = f"error: {type(e).__name__}: {e}"
        await self._finish(success, summary)
        return {"success": success, "summary": summary, "trace": self.trace[-12:], "steps": self.steps}

    async def _act(self, action: str, args: dict, allowed: dict) -> str:
        b = self.brain
        if action not in allowed:
            close = difflib.get_close_matches(action, list(allowed), n=3)
            b.lessons.log_failure(action or "?", "", "nonexistent tool")
            return (
                f"ERROR: tool '{action}' does not exist." + (f" Did you mean: {', '.join(close)}?" if close else "")
                + f" Available: {', '.join(allowed)}"
            )
        await self._state("acting", action)
        await b.bus.publish("tool.call", self.id, tool=action, args=_clip(args, 300))
        try:
            res = await b.tools.call(ToolContext(b, self), action, args)
            ok, obs = not (isinstance(res, dict) and res.get("ok") is False), _clip(res, 2500)
        except Exception as e:  # noqa: BLE001
            ok, obs = False, f"ERROR {type(e).__name__}: {str(e)[:900]}"
        args_s = json.dumps(args, ensure_ascii=False, default=str)[:160]
        if ok and action in self._fails:
            bad_args, err = self._fails.pop(action)
            b.lessons.mark_resolved(action)
            await b.lessons.add(f"{action}: the call {bad_args} failed with '{err}'; it worked with {args_s}", "fix")
        elif not ok:
            self._fails[action] = (args_s, obs[:140])
            b.lessons.log_failure(action, args_s, obs)
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
