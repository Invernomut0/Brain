"""ReAct-style agent: think -> act (tool) -> observe, until `finish`.

Context policy (see context.py): the model never sees an ever-growing transcript. Every request is rebuilt from
  1. a lean system prompt (role prompt, the tools this role may use, protocol, a few relevant lessons),
  2. the pinned task with its project state,
  3. a WORKING MEMORY block (every step in one line, noted facts, files, open errors, pinned messages),
  4. the latest raw steps that fit a character budget,
  5. a mission/status reminder as the very last lines.
"""
from __future__ import annotations

import difflib
import json
import time
import uuid
from typing import TYPE_CHECKING, Any

from .context import Step, WorkingMemory, mission, tokens
from .control import Halt
from .llm import LLMError
from .prompts import PROTOCOL
from .tools import ToolContext, _clip

if TYPE_CHECKING:
    from .core import Brain

# Tools every working role may use. The evolution tools (propose_prompt / propose_hook) belong to the evolver only:
# an executor changing prompts mid-task is how role prompts get polluted.
CORE_TOOLS = [
    "web_search", "web_fetch", "http_request", "create_venv", "python_exec", "shell_exec", "read_file", "write_file", "list_files",
    "remember", "recall", "wiki_search", "wiki_read", "wiki_note", "ask_user", "send_message", "spawn_agent",
    "spawn_parallel", "create_tool", "publish_artifact", "report_progress",
]
ROLE_TOOLS: dict[str, list[str]] = {
    "executor": CORE_TOOLS,
    "researcher": ["web_search", "web_fetch", "http_request", "remember", "recall", "wiki_search", "wiki_read", "wiki_note", "read_file", "write_file", "list_files", "create_venv", "python_exec", "publish_artifact", "report_progress"],
    "engineer": ["create_venv", "python_exec", "shell_exec", "read_file", "write_file", "list_files", "create_tool", "web_search", "web_fetch", "recall", "remember", "wiki_search", "wiki_read", "publish_artifact", "report_progress"],
    "evolver": ["propose_prompt", "propose_hook"],
}


def tools_for(role: str) -> list[str]:
    return ROLE_TOOLS.get(role, CORE_TOOLS)  # custom roles created with spawn_agent get the core set


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
        self.mem = WorkingMemory()
        self._fails: dict[str, tuple[str, str]] = {}
        self._system_msg = ""
        self._first = ""

    # -- prompt ------------------------------------------------------------
    def _system(self) -> str:
        b = self.brain
        base = self.custom_prompt or b.evolution.prompt(self.role)
        lessons = "\n".join(f"- {t}" for t in b.lessons.relevant(self.task, 3))
        learned = f"LESSONS LEARNED from past errors (do not repeat them):\n{lessons}\n\n" if lessons else ""
        return (
            f"{base}\n\nAvailable tools:\n{b.tools.describe(tools_for(self.role))}\n\n{PROTOCOL}\n\n{learned}"
            f"Your id: {self.id}. Role: {self.role}. You work for {b.owner}."
        )

    async def _state(self, state: str, detail: str = "") -> None:
        await self.brain.bus.publish("agent.state", self.id, state=state, detail=detail[:200])

    # -- context -----------------------------------------------------------
    def _build(self) -> list[dict]:
        """The exact messages for the next request; also recorded for the context inspector."""
        mem = self.mem.block()
        pairs = self.mem.pairs(self.brain.settings.agent_context_chars)
        reminder = mission(self.task, self.steps, self.max_steps)
        msgs = [{"role": "system", "content": self._system_msg}, {"role": "user", "content": self._first + (f"\n\n{mem}" if mem else "")}, *pairs]
        msgs[-1] = {"role": "user", "content": msgs[-1]["content"] + "\n\n" + reminder}  # the last message is always a user one
        self._record_context(mem, pairs, reminder)
        return msgs

    def _record_context(self, mem: str, pairs: list[dict], reminder: str) -> None:
        sections = [
            ("System prompt", self._system_msg), ("Task and project state", self._first), ("Working memory", mem),
            ("Recent steps", "\n".join(p["content"] for p in pairs)), ("Mission reminder", reminder),
        ]
        total = sum(len(t) for _, t in sections)
        self.brain.set_context(self.id, {
            "agent": self.id, "role": self.role, "step": self.steps, "max_steps": self.max_steps, "ts": time.time(),
            "budget_chars": self.brain.settings.agent_context_chars, "chars": total, "tokens": tokens(total),
            "sections": [{"name": n, "chars": len(t), "tokens": tokens(len(t))} for n, t in sections],
            "messages": [{"section": n, "chars": len(t), "text": t[:8000]} for n, t in sections if t],
        })

    def stats(self) -> dict:
        """How the run went, from its tool calls: repeated identical calls and failures are the visible signs of wandering."""
        seen: set[tuple[str, str]] = set()
        repeats = 0
        for t in self.trace:
            key = (t["tool"], t.get("sig", ""))
            repeats += key in seen
            seen.add(key)
        return {"steps": self.steps, "calls": len(self.trace), "repeats": repeats, "errors": sum(not t["ok"] for t in self.trace)}

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
        allowed = b.tools.all(tools_for(self.role))
        wiki_ctx = await b.wiki.context(self.task, 3) if "wiki_search" in allowed else ""
        self._first = f"TASK:\n{self.task}" + (f"\n\nKNOWLEDGE ALREADY IN THE WIKI (wiki_read for details, do not redo what is already known):\n{wiki_ctx}" if wiki_ctx else "")
        self._system_msg = self._system()
        success, summary = False, "no result"
        format_errors = 0
        try:
            for _ in range(self.max_steps):
                await b.control.gate()
                while self.inbox:
                    frm, txt = self.inbox.pop(0)
                    self.mem.pin(frm, txt)
                self.steps += 1
                msgs = self._build()
                await self._state("thinking")
                try:
                    step = await b.llm.chat_json(msgs, agent=self.id, depth=self.depth, purpose=f"{self.role} step {self.steps}", temperature=0.6, max_tokens=1800, retries=1)
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
                if action == "finish":
                    success = bool(args.get("success", False))
                    summary = str(args.get("summary", ""))[:2000]
                    break
                obs = await self._act(action, args, allowed)
                self.mem.add(Step(
                    self.steps, json.dumps(step, ensure_ascii=False)[:2500], action, json.dumps(args, ensure_ascii=False, default=str)[:400],
                    self.trace[-1]["ok"] if self.trace else False, obs, str(step.get("note", "")),
                ))
                if self.steps >= self.max_steps:  # the last step ran a tool instead of finishing: stop with what exists
                    summary = f"step limit reached without finish. Partial: {self.mem.summary()}"
                    break
            else:
                summary = f"step limit reached without finish. Partial: {self.mem.summary()}"
        except Halt:
            summary = "interrupted"
            await self._finish(False, summary)
            raise
        except LLMError as e:
            summary = f"LLM error: {e}"
        except Exception as e:  # noqa: BLE001
            summary = f"error: {type(e).__name__}: {e}"
        await self._finish(success, summary)
        return {"success": success, "summary": summary, "trace": self.trace[-12:], "steps": self.steps, "stats": self.stats()}

    async def _act(self, action: str, args: dict, allowed: dict) -> str:
        b = self.brain
        if action not in allowed:
            close = difflib.get_close_matches(action, list(allowed), n=3)
            b.lessons.log_failure(action or "?", "", "nonexistent tool")
            self.trace.append({"tool": action or "?", "ok": False, "obs": "nonexistent tool", "sig": ""})
            return (
                f"ERROR: tool '{action}' does not exist." + (f" Did you mean: {', '.join(close)}?" if close else "")
                + f" Available: {', '.join(allowed)}"
            )
        await self._state("acting", action)
        await b.bus.publish("tool.call", self.id, tool=action, args=_clip(args, 300))
        t0 = time.time()
        try:
            res = await b.tools.call(ToolContext(b, self), action, args)
            ok, obs = not (isinstance(res, dict) and res.get("ok") is False), _clip(res, 2500)
            full = res
        except Exception as e:  # noqa: BLE001
            ok, obs = False, f"ERROR {type(e).__name__}: {str(e)[:900]}"
            full = f"ERROR {type(e).__name__}: {e}"
        run_id = b.results.record_tool_run(self.id, self.goal_id, action, args, ok, round((time.time() - t0) * 1000), full)
        args_s = json.dumps(args, ensure_ascii=False, default=str)[:160]
        if ok and action in self._fails:
            bad_args, err = self._fails.pop(action)
            b.lessons.mark_resolved(action)
            await b.lessons.add(f"{action}: the call {bad_args} failed with '{err}'; it worked with {args_s}", "fix")
        elif not ok:
            self._fails[action] = (args_s, obs[:140])
            b.lessons.log_failure(action, args_s, obs)
        self.trace.append({"tool": action, "ok": ok, "obs": obs[:300], "sig": args_s[:80]})
        await b.bus.publish("tool.result", self.id, tool=action, ok=ok, preview=obs[:200], run=run_id)
        return obs

    async def _finish(self, success: bool, summary: str) -> None:
        b = self.brain
        b.db.execute(
            "UPDATE agents SET status=?, ended=?, steps=?, result=? WHERE id=?",
            ("done" if success else "failed", time.time(), self.steps, summary[:1000], self.id),
        )
        await b.bus.publish("agent.end", self.id, success=success, summary=summary[:500], steps=self.steps)
