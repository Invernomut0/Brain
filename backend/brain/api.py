"""FastAPI app: REST controls + WebSocket event stream + static dashboard."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import ROOT
from .core import Brain
from .projects import ProjectError, ProjectNotFound

DIST = ROOT / "frontend" / "dist"


class ChatIn(BaseModel):
    text: str


class ResetIn(BaseModel):
    confirm: str


class MainGoalIn(BaseModel):
    text: str
    archive_pending: bool = True


class BudgetIn(BaseModel):
    max_cycles: int | None = None
    max_tokens: int | None = None


class ProjectSaveIn(BaseModel):
    name: str | None = None


class ProjectLoadIn(BaseModel):
    save_current: bool = True


class ProjectNewIn(BaseModel):
    name: str
    main_goal: str | None = None
    save_current: bool = True
    owner: str | None = None


class OwnerIn(BaseModel):
    name: str


def create_app(brain: Brain | None = None) -> FastAPI:
    brain = brain or Brain()

    async def _project_call(coro):
        try:
            return await coro
        except ProjectNotFound as e:
            raise HTTPException(404, "project not found") from e
        except (ProjectError, ValueError) as e:
            raise HTTPException(422, str(e)) from e

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await brain.startup()
        yield
        await brain.shutdown()

    app = FastAPI(title="Brain", version="0.1.23", lifespan=lifespan)
    app.state.brain = brain
    app.add_middleware(
        CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["*"], allow_headers=["*"],
    )

    @app.get("/api/v1/state")
    async def state():
        return brain.snapshot()

    @app.post("/api/v1/control/{action}")
    async def control(action: str):
        fn = {"start": brain.start, "pause": brain.pause, "resume": brain.resume, "stop": brain.stop, "kill": brain.kill}.get(action)
        if not fn:
            raise HTTPException(404, "unknown action")
        await fn()
        return brain.control.snapshot()

    @app.get("/api/v1/main-goal")
    async def get_main_goal():
        root = brain.goals.root() or {}
        return {"title": root.get("title"), "text": root.get("description")}

    @app.put("/api/v1/main-goal")
    async def put_main_goal(body: MainGoalIn):
        try:
            root = await brain.set_main_goal(body.text, body.archive_pending)
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        return {"title": root["title"], "text": root["description"], "cancelled": root["cancelled"]}

    @app.get("/api/v1/owner")
    async def get_owner():
        return {"name": brain.owner_name}

    @app.put("/api/v1/owner")
    async def put_owner(body: OwnerIn):
        try:
            return {"name": await brain.set_owner(body.name)}
        except ValueError as e:
            raise HTTPException(422, str(e)) from e

    @app.post("/api/v1/reset")
    async def reset(body: ResetIn):
        if body.confirm != "RESET":
            raise HTTPException(400, 'confirmation required: send {"confirm": "RESET"}')
        await brain.reset()
        return brain.control.snapshot()

    @app.post("/api/v1/budget")
    async def budget(b: BudgetIn):
        await brain.control.set_budget(b.max_cycles, b.max_tokens)
        return brain.control.snapshot()

    @app.post("/api/v1/chat")
    async def chat(msg: ChatIn):
        text = msg.text.strip()
        if not text or len(text) > 4000:
            raise HTTPException(422, "message must be 1-4000 chars")
        await brain.bus.publish("chat.message", None, role="user", text=text)
        task = asyncio.create_task(brain.orchestrator.handle_user_message(text))
        brain.orchestrator._bg.add(task)
        task.add_done_callback(brain.orchestrator._bg.discard)
        return {"ok": True}

    @app.get("/api/v1/memory")
    async def memory(q: str, k: int = 5):
        return await brain.memory.search(q, k)

    @app.get("/api/v1/memories")
    async def memories(q: str = "", kind: str | None = None, limit: int = 100, offset: int = 0):
        limit = max(1, min(limit, 500))
        items = await brain.memory.find(q.strip()[:500], kind, limit) if q.strip() else brain.memory.list(kind, limit, max(offset, 0))
        return {"items": items, "kinds": brain.memory.kinds(), "total": brain.memory.count()}

    @app.get("/api/v1/status")
    async def status():
        return brain.status.current()

    @app.post("/api/v1/status/refresh")
    async def status_refresh():
        return await brain.status.refresh()

    @app.get("/api/v1/wiki")
    async def wiki_stats():
        return await brain.wiki.stats()

    @app.get("/api/v1/wiki/graph")
    async def wiki_graph():
        return brain.wiki.graph()

    @app.get("/api/v1/wiki/page")
    async def wiki_page(id: str):
        page = brain.wiki.read_page(id.strip().removesuffix(".md"))
        if not page:
            raise HTTPException(404, "page not found")
        return page

    @app.get("/api/v1/wiki/search")
    async def wiki_search(q: str, k: int = 8):
        return await brain.wiki.search(q.strip()[:500], max(1, min(k, 30)))

    @app.post("/api/v1/wiki/ingest")
    async def wiki_ingest():
        """Sync database-derived pages now and fold pending memories/journal entries into the wiki (LLM)."""
        await brain.wiki.sync_all()
        res = await brain.wiki.ingest(passes=3)
        await brain.wiki.sync_all()
        return {**res, "stats": await brain.wiki.stats()}

    @app.post("/api/v1/wiki/embed")
    async def wiki_embed():
        """Vectorise pages and memories with BRAIN_EMBED_MODEL, letting LM Studio load it if needed."""
        res = await brain.wiki.embed_pending(force=True)
        return {**res, "stats": await brain.wiki.stats()}

    @app.get("/api/v1/projects")
    async def projects_list():
        return {"current": brain.projects.current(), "items": brain.projects.list()}

    @app.post("/api/v1/projects/save")
    async def projects_save(body: ProjectSaveIn):
        return await _project_call(brain.projects.save(body.name))

    @app.post("/api/v1/projects/new")
    async def projects_new(body: ProjectNewIn):
        return await _project_call(brain.projects.new(body.name, body.main_goal, body.save_current, body.owner))

    @app.post("/api/v1/projects/{pid}/load")
    async def projects_load(pid: str, body: ProjectLoadIn | None = None):
        return await _project_call(brain.projects.load(pid, body.save_current if body else True))

    @app.delete("/api/v1/projects/{pid}")
    async def projects_delete(pid: str):
        try:
            brain.projects.delete(pid)
        except ProjectNotFound as e:
            raise HTTPException(404, "project not found") from e
        return {"deleted": pid}

    @app.get("/api/v1/events")
    async def events(limit: int = 200):
        return brain.bus.recent(min(limit, 1000))

    @app.websocket("/ws")
    async def ws(sock: WebSocket):
        await sock.accept()
        q = brain.bus.subscribe()
        try:
            await sock.send_json({"type": "snapshot", "data": brain.snapshot()})
            while True:
                ev = await q.get()
                await sock.send_json(ev.to_dict())
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            brain.bus.unsubscribe(q)

    if DIST.exists():
        app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

        @app.get("/{path:path}")
        async def spa(path: str):
            if path.startswith("api/"):
                raise HTTPException(404, "unknown API endpoint (is the server running an older version? restart it)")
            f = DIST / path
            return FileResponse(f if f.is_file() else DIST / "index.html")

    return app
