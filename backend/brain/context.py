"""Context engineering for agents: a working memory that replaces blind history trimming, and the project state
digest handed to every goal. Nothing here calls the model: it is plain bookkeeping, so it is cheap and testable.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .core import Brain

CHARS_PER_TOKEN = 3.6
OLD_OBS = 500  # observation length once a step is no longer among the latest ones
LAST_OBS = 2500
MAX_FACTS = 14
MAX_LISTED_STEPS = 18


def tokens(chars: int) -> int:
    return round(chars / CHARS_PER_TOKEN)


def one_line(s: object, n: int = 140) -> str:
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[: n - 1] + "…"


@dataclass
class Step:
    n: int
    assistant: str  # the JSON the model produced
    action: str
    args: str
    ok: bool
    obs: str  # observation as the model saw it (already clipped)
    note: str = ""


@dataclass
class WorkingMemory:
    """What an agent knows about its own run: every step in one line, the facts it noted, files written, open errors."""

    steps: list[Step] = field(default_factory=list)
    facts: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)
    pins: list[str] = field(default_factory=list)  # messages from the owner or other agents: never dropped

    def pin(self, who: str, text: str) -> None:
        self.pins = (self.pins + [f"{who}: {one_line(text, 400)}"])[-5:]

    def add(self, step: Step) -> None:
        self.steps.append(step)
        if step.note:
            note = one_line(step.note, 220)
            if note not in self.facts:
                self.facts = (self.facts + [note])[-MAX_FACTS:]
        if step.action in ("write_file", "publish_artifact") and step.ok:
            path = str(json.loads(step.args).get("path", "")) if step.args.startswith("{") else ""
            if path and path not in self.files:
                self.files = (self.files + [path])[-10:]
        if step.ok:
            self.errors.pop(step.action, None)
        else:
            self.errors[step.action] = one_line(step.obs, 160)

    def block(self) -> str:
        """The compact summary pinned right after the task: it is what survives when raw steps are dropped."""
        if not (self.steps or self.pins):
            return ""
        lines = ["WORKING MEMORY (your own run so far: build on it, do not repeat finished work)"]
        if self.steps:
            shown = self.steps[-MAX_LISTED_STEPS:]
            if len(self.steps) > len(shown):
                lines.append(f"({len(self.steps) - len(shown)} earlier steps omitted)")
            for s in shown:
                lines.append(f"{s.n}. {s.action}({one_line(s.args, 90)}) -> {'ok' if s.ok else 'FAILED'}: {one_line(s.obs, 110)}")
        if self.facts:
            lines.append("Learned: " + " | ".join(self.facts))
        if self.files:
            lines.append("Files written: " + ", ".join(self.files))
        if self.errors:
            lines.append("Still failing: " + "; ".join(f"{k}: {v}" for k, v in self.errors.items()))
        if self.pins:
            lines.append("Pinned messages:\n" + "\n".join(f"- {p}" for p in self.pins))
        return "\n".join(lines)

    def pairs(self, budget: int) -> list[dict]:
        """Recent raw steps as chat messages, newest first until the character budget is spent (the latest always fits)."""
        out: list[list[dict]] = []
        used = 0
        for i, s in enumerate(reversed(self.steps)):
            limit = LAST_OBS if i == 0 else OLD_OBS
            obs = s.obs if len(s.obs) <= limit else s.obs[:limit] + f"... [{len(s.obs) - limit} more characters: see WORKING MEMORY]"
            asst = s.assistant if i == 0 else one_line(s.assistant, 400)
            pair = [{"role": "assistant", "content": asst}, {"role": "user", "content": f"OBSERVATION ({s.action}):\n{obs}"}]
            size = len(asst) + len(obs)
            if out and used + size > budget:
                break
            out.append(pair)
            used += size
        return [m for pair in reversed(out) for m in pair]

    def summary(self) -> str:
        """One paragraph for an agent that ran out of steps."""
        done = ", ".join(f"{s.action}{'' if s.ok else ' (failed)'}" for s in self.steps[-8:])
        facts = " ".join(self.facts[-4:])
        return one_line(f"{len(self.steps)} steps ({done}). {facts}", 600)


def mission(task: str, step: int, max_steps: int) -> str:
    """Reminder placed at the very end of the context, where the model pays most attention."""
    left = max_steps - step
    status = f"[STATUS] step {step}/{max_steps}, {left} left after this one."
    if left <= 0:
        status += " LAST STEP: reply with action \"finish\" now (success=false with the partial result if you are not done)."
    elif left <= 2:
        status += " Wrap up: publish what you have (publish_artifact / report_progress) and finish."
    return f"[MISSION] {one_line(task, 380)}\n{status}"


async def project_state(b: "Brain", goal: dict, query: str, limit: int = 2600) -> str:
    """Verified facts a goal should build on: progress, results of the goals it depends on, artifacts, memories, owner messages."""
    parts: list[str] = []
    prog = b.results.progress()
    latest = prog["latest"]
    if latest:
        parts.append(f"Progress toward the root goal: {latest['percent']:.0f}% - {one_line(latest['summary'], 200)}")
        done = [m["title"] for m in prog["milestones"] if m["done"]]
        todo = [m["title"] for m in prog["milestones"] if not m["done"]]
        if done:
            parts.append("Milestones reached: " + "; ".join(done[:6]))
        if todo:
            parts.append("Milestones still open: " + "; ".join(todo[:6]))

    deps = b.goals.deps(goal)
    if deps:
        rows = [b.goals.get(i) for i in deps]
        head = "Results of the goals this one depends on:"
    else:
        rows = b.db.query(
            "SELECT * FROM goals WHERE status='done' AND parent_id IS NOT NULL AND id<>? ORDER BY updated DESC LIMIT 3", (goal.get("id", 0),)
        )
        head = "Latest goals completed:"
    lines = [f"- #{g['id']} {g['title'][:80]} [{g['status']}]: {one_line(g.get('result') or '(no result recorded)', 300)}" for g in rows if g]
    if lines:
        parts.append(head + "\n" + "\n".join(lines))

    arts = b.results.artifacts(5)
    if arts:
        parts.append("Artifacts already published: " + "; ".join(f"{a['title']} ({a['path']})" for a in arts))

    try:
        mems = await b.memory.search(query, 3)
    except Exception:  # noqa: BLE001 - the digest is a help, never a reason to fail a goal
        mems = []
    facts = [one_line(m["text"], 220) for m in mems if m["kind"] in ("fact", "insight", "user", "decision")]
    if facts:
        parts.append("Relevant memory:\n" + "\n".join(f"- {f}" for f in facts))

    said = [e["data"].get("text", "") for e in b.bus.recent(40, ["chat.message"]) if e["data"].get("role") == "user"][-3:]
    if said:
        parts.append(f"{b.owner} said recently:\n" + "\n".join(f"- {one_line(t, 200)}" for t in said))

    text = "\n".join(parts)
    return text if len(text) <= limit else text[: limit - 1] + "…"
