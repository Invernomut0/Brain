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
            return await tool.fn(ctx, **args)
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
    base = ctx.brain.settings.workspace_dir.resolve()
    p = (base / rel).resolve()
    if base != p and base not in p.parents:
        raise PermissionError("path fuori dal workspace")
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


async def ask_user(ctx: ToolContext, message: str):
    await ctx.brain.bus.publish("chat.message", ctx.agent.id, role="brain", text=message, agent_role=ctx.agent.role)
    return "messaggio inviato a Lorenzo; l'eventuale risposta arrivera' come messaggio utente nei prossimi cicli"


async def send_message(ctx: ToolContext, to: str, text: str):
    ok = ctx.brain.orchestrator.deliver(ctx.agent.id, to, text)
    return "consegnato" if ok else f"agente {to} non trovato"


async def spawn_agent(ctx: ToolContext, role: str, task: str, system_prompt: str = ""):
    return await ctx.brain.orchestrator.spawn_and_run(ctx.agent, role, task, system_prompt)


# ---------------------------------------------------------------- evolution
async def create_tool(ctx: ToolContext, name: str, description: str, params: dict, code: str, test_code: str):
    b = ctx.brain
    if not NAME_RE.match(name) or name in b.tools.builtin:
        return {"ok": False, "error": "nome non valido o gia' usato da un tool built-in (a-z, 0-9, _; 3-31 caratteri)"}
    if "def run(" not in code:
        return {"ok": False, "error": "il codice deve definire run(**kwargs)"}
    if "def test_" not in test_code:
        return {"ok": False, "error": "servono test pytest (funzioni test_*) reali"}
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
        return {"ok": False, "error": "test falliti, tool NON registrato", "output": res.brief()[-1200:]}
    return {"ok": True, "message": f"tool '{name}' registrato e disponibile da ora", "tests": res.brief()[-300:]}


async def propose_prompt(ctx: ToolContext, role: str, new_prompt: str, reason: str):
    return await ctx.brain.evolution.propose_prompt(role, new_prompt, reason)


async def propose_hook(ctx: ToolContext, name: str, code: str, test_code: str, reason: str):
    return await ctx.brain.evolution.propose_hook(name, code, test_code, reason)


def _builtin_tools() -> list[Tool]:
    T = Tool
    return [
        T("web_search", "cerca su internet (DuckDuckGo); ritorna titolo/url/snippet", {"query": "str", "max_results": "int=6"}, web_search),
        T("web_fetch", "scarica una pagina web e ne estrae il testo e i link", {"url": "str", "max_chars": "int=6000"}, web_fetch),
        T("http_request", "richiesta HTTP generica (GET/POST/...) a qualunque URL/API", {"method": "str", "url": "str", "headers": "dict?", "body": "json?"}, http_request),
        T("python_exec", "esegue codice Python nella sandbox Podman (rete attiva, /workspace persistente)", {"code": "str"}, python_exec),
        T("shell_exec", "esegue un comando shell nella sandbox Podman", {"command": "str"}, shell_exec),
        T("read_file", "legge un file del workspace", {"path": "str"}, read_file),
        T("write_file", "scrive un file nel workspace", {"path": "str", "content": "str"}, write_file),
        T("list_files", "elenca i file del workspace", {"path": "str='.'"}, list_files),
        T("remember", "salva un fatto nella memoria a lungo termine", {"text": "str", "tags": "list?", "importance": "0-1"}, remember),
        T("recall", "cerca nella memoria a lungo termine (semantica)", {"query": "str", "k": "int=5"}, recall),
        T("ask_user", "scrive a Lorenzo nella chat (non bloccante)", {"message": "str"}, ask_user),
        T("send_message", "invia un messaggio a un altro agente vivo", {"to": "agent_id", "text": "str"}, send_message),
        T("spawn_agent", "crea un sotto-agente con un ruolo e attende il risultato", {"role": "str", "task": "str", "system_prompt": "str? (ruolo personalizzato)"}, spawn_agent),
        T("create_tool", "crea un nuovo tool Python permanente: code definisce run(**kwargs); test_code sono test pytest che importano il modulo (sys.path.insert(0,'/workspace/tools'))",
          {"name": "snake_case", "description": "str", "params": "dict nome->descrizione", "code": "str", "test_code": "str"}, create_tool),
        T("propose_prompt", f"modifica il prompt di un ruolo (planner/executor/researcher/engineer/critic/reflector); versionato con rollback automatico", {"role": "str", "new_prompt": "str", "reason": "str"}, propose_prompt),
        T("propose_hook", "modifica il codice di un hook evolvibile (prioritize.prioritize(goals,state)->ids | context.build_context(state)->str) con test; eseguito in sandbox", {"name": "prioritize|context", "code": "str", "test_code": "str", "reason": "str"}, propose_hook),
    ]
