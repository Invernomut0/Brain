"""Tool registry: built-in tools plus agent-authored tools that run in the Podman sandbox."""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable
from urllib.parse import parse_qs, unquote, urlparse

import httpx
from bs4 import BeautifulSoup

if TYPE_CHECKING:
    from .agents import Agent
    from .core import Brain

NAME_RE = re.compile(r"^[a-z][a-z0-9_]{2,30}$")
UA = {"User-Agent": "Mozilla/5.0 (Brain autonomous agent)"}


@dataclass
class ToolContext:
    brain: "Brain"
    agent: "Agent"


@dataclass
class Tool:
    name: str
    description: str
    params: dict[str, str]
    fn: Callable[..., Awaitable[Any]] | None = None
    custom: bool = False

    def signature(self) -> str:
        args = ", ".join(f"{k}: {v}" for k, v in self.params.items())
        return f"- {self.name}({args}) - {self.description}"


class ToolRegistry:
    def __init__(self, brain: "Brain"):
        self.brain = brain
        self.builtin: dict[str, Tool] = {}
        for t in _builtin_tools():
            self.builtin[t.name] = t

    def custom(self) -> dict[str, Tool]:
        rows = self.brain.db.query("SELECT * FROM tools WHERE status='active'")
        return {
            r["name"]: Tool(r["name"], r["description"], json.loads(r["params"] or "{}"), custom=True)
            for r in rows
        }

    def all(self, names: list[str] | None = None) -> dict[str, Tool]:
        tools = {**self.builtin, **self.custom()}
        if names is not None:
            tools = {k: v for k, v in tools.items() if k in names or v.custom}
        return tools

    def describe(self, names: list[str] | None = None) -> str:
        return "\n".join(t.signature() for t in self.all(names).values())

    async def call(self, ctx: ToolContext, name: str, args: dict) -> Any:
        tool = self.builtin.get(name)
        if tool and tool.fn:
            try:
                return await tool.fn(ctx, **args)
            except TypeError as e:  # wrong/missing arguments: tell the model the exact signature
                raise TypeError(f"{e}. Correct signature: {tool.signature()}") from e
        custom = self.custom().get(name)
        if not custom:
            raise KeyError(f"tool '{name}' not found")
        res = await self.brain.sandbox.run_tool(name, args)
        failed = not res.get("ok")
        self.brain.db.execute(
            "UPDATE tools SET calls=calls+1, failures=failures+? WHERE name=?", (1 if failed else 0, name)
        )
        if failed:
            raise RuntimeError(str(res.get("error"))[:1200])
        return res.get("result")


# ---------------------------------------------------------------- helpers
def _clip(v: Any, n: int = 6000) -> Any:
    s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, default=str)
    return s if len(s) <= n else s[:n] + f"... [troncato {len(s) - n} caratteri]"


def _safe_path(ctx: ToolContext, rel: str) -> Path:
    # In the sandbox the workspace is mounted at /workspace: accept those paths as relative ones.
    rel = str(rel).strip()
    for prefix in ("/workspace/", "workspace/", "./"):
        if rel.startswith(prefix):
            rel = rel[len(prefix):]
    if rel in ("/workspace", "workspace"):
        rel = "."
    rel = rel.lstrip("/") or "."
    base = ctx.brain.settings.workspace_dir.resolve()
    p = (base / rel).resolve()
    if base != p and base not in p.parents:
        raise PermissionError("path outside the workspace: use paths RELATIVE to the workspace (e.g. 'notes/a.txt'; in the sandbox the workspace is /workspace) without '..'")
    return p


# ---------------------------------------------------------------- web
async def web_search(ctx: ToolContext, query: str, max_results: int = 6):
    async with httpx.AsyncClient(headers=UA, timeout=25, follow_redirects=True) as c:
        r = await c.post("https://html.duckduckgo.com/html/", data={"q": query})
        r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    out = []
    for res in soup.select(".result")[: int(max_results)]:
        a = res.select_one(".result__a")
        if not a:
            continue
        href = a.get("href", "")
        q = parse_qs(urlparse(href).query)
        url = unquote(q["uddg"][0]) if "uddg" in q else href
        sn = res.select_one(".result__snippet")
        out.append({"title": a.get_text(strip=True), "url": url, "snippet": sn.get_text(" ", strip=True) if sn else ""})
    return out


async def web_fetch(ctx: ToolContext, url: str, max_chars: int = 6000):
    async with httpx.AsyncClient(headers=UA, timeout=30, follow_redirects=True) as c:
        r = await c.get(url)
    ctype = r.headers.get("content-type", "")
    if "html" in ctype:
        soup = BeautifulSoup(r.text, "html.parser")
        for t in soup(["script", "style", "nav", "footer", "noscript"]):
            t.decompose()
        text = re.sub(r"\n{3,}", "\n\n", soup.get_text("\n", strip=True))
        links = [a["href"] for a in soup.select("a[href^=http]")][:15]
        return {"status": r.status_code, "text": _clip(text, int(max_chars)), "links": links}
    return {"status": r.status_code, "content_type": ctype, "text": _clip(r.text, int(max_chars))}


async def http_request(ctx: ToolContext, method: str, url: str, headers: dict | None = None, body: Any = None):
    kw: dict[str, Any] = {}
    if body is not None:
        kw["json" if isinstance(body, (dict, list)) else "content"] = body
    async with httpx.AsyncClient(headers={**UA, **(headers or {})}, timeout=30, follow_redirects=True) as c:
        r = await c.request(method.upper(), url, **kw)
    return {"status": r.status_code, "headers": dict(list(r.headers.items())[:12]), "body": _clip(r.text, 5000)}


# ---------------------------------------------------------------- sandbox & files
async def python_exec(ctx: ToolContext, code: str):
    return (await ctx.brain.sandbox.python(code)).brief()


async def shell_exec(ctx: ToolContext, command: str):
    return (await ctx.brain.sandbox.shell(command)).brief()


async def read_file(ctx: ToolContext, path: str):
    return _clip(_safe_path(ctx, path).read_text(errors="replace"), 8000)


async def write_file(ctx: ToolContext, path: str, content: str):
    p = _safe_path(ctx, path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return f"scritti {len(content)} caratteri in {path}"


async def list_files(ctx: ToolContext, path: str = "."):
    base = _safe_path(ctx, path)
    return sorted(str(p.relative_to(ctx.brain.settings.workspace_dir)) for p in base.rglob("*") if p.is_file())[:200]


# ---------------------------------------------------------------- memory & dialogue
async def remember(ctx: ToolContext, text: str, tags: list[str] | None = None, importance: float = 0.6):
    mid = await ctx.brain.memory.add("fact", text, tags or [], float(importance))
    return f"memorizzato #{mid}"


async def recall(ctx: ToolContext, query: str, k: int = 5):
    return await ctx.brain.memory.search(query, int(k))


async def wiki_search(ctx: ToolContext, query: str, k: int = 5):
    return await ctx.brain.wiki.search(query, min(int(k), 10))


async def wiki_read(ctx: ToolContext, id: str):
    page = ctx.brain.wiki.read_page(str(id).strip().removesuffix(".md"))
    if not page:
        return f"page '{id}' not found: use wiki_search to find the ids"
    return {"id": page["id"], "title": page["title"], "body": _clip(page["body"], 5000), "links": [l["id"] or l["target"] for l in page["links"]]}


async def wiki_note(ctx: ToolContext, title: str, body: str, summary: str = ""):
    try:
        pid = await ctx.brain.wiki.note(title, body, summary, f"agent:{ctx.agent.id}")
    except ValueError as e:
        return f"ERROR: {e}"
    return f"filed as [[{pid}]]"


async def ask_user(ctx: ToolContext, message: str, wait: int = 120):
    reply = await ctx.brain.orchestrator.ask(ctx.agent, message, min(max(int(wait), 0), 300))
    o = ctx.brain.owner
    if reply is not None:
        return f"{o}'s reply: {reply}"
    return (
        f"Message sent but {o} has not replied (yet): proceed autonomously with reasonable assumptions, "
        f"state them in the summary; if they reply later you will receive a [{o}] message or the goal will be reopened."
    )


async def send_message(ctx: ToolContext, to: str, text: str):
    ok = ctx.brain.orchestrator.deliver(ctx.agent.id, to, text)
    return "delivered" if ok else f"agent {to} not found"


async def spawn_agent(ctx: ToolContext, role: str, task: str, system_prompt: str = ""):
    return await ctx.brain.orchestrator.spawn_and_run(ctx.agent, role, task, system_prompt)


async def spawn_parallel(ctx: ToolContext, tasks: list):
    return await ctx.brain.orchestrator.spawn_many(ctx.agent, tasks)


# ---------------------------------------------------------------- evolution
async def create_tool(ctx: ToolContext, name: str, description: str, params: dict, code: str, test_code: str):
    b = ctx.brain
    if not NAME_RE.match(name) or name in b.tools.builtin:
        return {"ok": False, "error": "invalid name or already used by a built-in tool (a-z, 0-9, _; 3-31 characters)"}
    if "def run(" not in code:
        return {"ok": False, "error": "the code must define run(**kwargs)"}
    if "def test_" not in test_code:
        return {"ok": False, "error": "real pytest tests (test_* functions) are required"}
    tdir = b.settings.workspace_dir / "tools"
    tdir.mkdir(parents=True, exist_ok=True)
    (tdir / f"{name}.py").write_text(code)
    (tdir / f"test_{name}.py").write_text(test_code)
    res = await b.sandbox.pytest(f"/workspace/tools/test_{name}.py")
    passed = res.ok
    b.db.execute(
        "INSERT INTO tools(name,description,params,status,created,test_output) VALUES(?,?,?,?,?,?) "
        "ON CONFLICT(name) DO UPDATE SET description=excluded.description, params=excluded.params, "
        "status=excluded.status, test_output=excluded.test_output",
        (name, description[:300], json.dumps(params or {}), "active" if passed else "rejected", time.time(), res.brief()[-1500:]),
    )
    await b.bus.publish("tool.created", ctx.agent.id, name=name, description=description, passed=passed)
    if not passed:
        return {"ok": False, "error": "tests failed, tool NOT registered", "output": res.brief()[-1200:]}
    return {"ok": True, "message": f"tool '{name}' registered and available from now on", "tests": res.brief()[-300:]}


async def propose_prompt(ctx: ToolContext, role: str, new_prompt: str, reason: str):
    return await ctx.brain.evolution.propose_prompt(role, new_prompt, reason)


async def propose_hook(ctx: ToolContext, name: str, code: str, test_code: str, reason: str):
    return await ctx.brain.evolution.propose_hook(name, code, test_code, reason)


def _builtin_tools() -> list[Tool]:
    T = Tool
    return [
        T("web_search", "search the internet (DuckDuckGo); returns title/url/snippet", {"query": "str", "max_results": "int=6"}, web_search),
        T("web_fetch", "download a web page and extract its text and links", {"url": "str", "max_chars": "int=6000"}, web_fetch),
        T("http_request", "generic HTTP request (GET/POST/...) to any URL/API", {"method": "str", "url": "str", "headers": "dict?", "body": "json?"}, http_request),
        T("python_exec", "run Python code in the Podman sandbox (network enabled, persistent /workspace)", {"code": "str"}, python_exec),
        T("shell_exec", "run a shell command in the Podman sandbox", {"command": "str"}, shell_exec),
        T("read_file", "read a workspace file (relative path; = /workspace in the sandbox)", {"path": "str"}, read_file),
        T("write_file", "write a workspace file (relative path, e.g. 'tools/x.py'; = /workspace in the sandbox)", {"path": "str", "content": "str"}, write_file),
        T("list_files", "list the workspace files (relative path)", {"path": "str='.'"}, list_files),
        T("remember", "save a fact in long-term memory", {"text": "str", "tags": "list?", "importance": "0-1"}, remember),
        T("recall", "search long-term memory (semantic)", {"query": "str", "k": "int=5"}, recall),
        T("wiki_search", "search Brain's wiki (already compiled knowledge: concepts, phases, decisions, episodes, tools); returns id, title, summary", {"query": "str", "k": "int=5"}, wiki_search),
        T("wiki_read", "read a wiki page given its id (e.g. 'concepts/sqlite')", {"id": "str"}, wiki_read),
        T("wiki_note", "file a useful answer or discovery in the wiki (permanent note, linkable with [[id]])", {"title": "str", "body": "markdown", "summary": "str?"}, wiki_note),
        T("ask_user", "write to the owner in the chat and WAIT for their reply (wait seconds, default 120, max 300, 0 = do not wait); returns the reply or a no-reply notice", {"message": "str", "wait": "int=120"}, ask_user),
        T("send_message", "send a message to another live agent", {"to": "agent_id", "text": "str"}, send_message),
        T("spawn_agent", "create a sub-agent with a role and wait for its result", {"role": "str", "task": "str", "system_prompt": "str? (custom role)"}, spawn_agent),
        T("spawn_parallel", "launch up to 4 sub-agents IN PARALLEL and wait for all the results", {"tasks": "list of {role, task, system_prompt?}"}, spawn_parallel),
        T("create_tool", "create a new permanent Python tool: code defines run(**kwargs); test_code are pytest tests that import the module (sys.path.insert(0,'/workspace/tools'))",
          {"name": "snake_case", "description": "str", "params": "dict name->description", "code": "str", "test_code": "str"}, create_tool),
        T("propose_prompt", "change the prompt of a role (planner/executor/researcher/engineer/critic/reflector); versioned with automatic rollback", {"role": "str", "new_prompt": "str", "reason": "str"}, propose_prompt),
        T("propose_hook", "change the code of an evolvable hook (prioritize.prioritize(goals,state)->ids | context.build_context(state)->str) with tests; runs in the sandbox", {"name": "prioritize|context", "code": "str", "test_code": "str", "reason": "str"}, propose_hook),
    ]
