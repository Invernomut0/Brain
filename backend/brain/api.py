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


def create_app(brain: Brain | None = None) -> FastAPI:
    brain = brain or Brain()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await brain.startup()
        yield
        await brain.shutdown()

    app = FastAPI(title="Brain", version="0.1.16", lifespan=lifespan)
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
            f = DIST / path
            return FileResponse(f if f.is_file() else DIST / "index.html")

    return app
