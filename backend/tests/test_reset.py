import pytest
from fastapi.testclient import TestClient

from brain.api import create_app
from brain.core import Brain
from brain.prompts import DEFAULTS
from brain.tools import ToolContext
from brain.agents import Agent

TOOL = "def run(x):\n    return {'y': x * 2}\n"
TEST = "from dbl import run\n\ndef test_run():\n    assert run(2) == {'y': 4}\n"


async def test_reset_returns_to_a_brand_new_installation(brain):
    ctx = ToolContext(brain, Agent(brain, "engineer", "t"))
    brain.settings.autostart = False  # with autostart on, a reset restarts the system by itself
    await brain._bootstrap()
    root = brain.goals.root()["id"]
    await brain.goals.add("g1", "", root)
    await brain.memory.add("fact", "Rome is the capital", ["geo"])
    brain.memory.journal_add("reflection", "I learned something")
    await brain.lessons.add("Always use relative paths for write_file", "fix")
    await brain.selfmodel.update({"hypotheses": ["maybe I am conscious"]})
    assert (await brain.tools.call(ctx, "create_tool", dict(name="dbl", description="d", params={"x": "int"}, code=TOOL, test_code=TEST)))["ok"]
    await brain.tools.call(ctx, "write_file", {"path": "notes/a.txt", "content": "x"})
    assert (await brain.evolution.propose_prompt("executor", "You are an experimental executor. " * 6, "test"))["ok"]
    brain.control.cycle = 7

    await brain.reset()

    assert [g["parent_id"] for g in brain.goals.all()] == [None]
    assert brain.memory.count() == 0 and brain.memory.journal_recent() == []
    assert brain.lessons.count() == 0 and brain.selfmodel.get()["revision"] == 0
    assert brain.tools.custom() == {} and brain.db.query("SELECT * FROM tools") == []
    assert list(brain.settings.workspace_dir.iterdir()) == [brain.settings.workspace_dir / "tools"]
    assert list((brain.settings.workspace_dir / "tools").iterdir()) == []
    assert brain.evolution.prompt("executor") == DEFAULTS["executor"]
    assert brain.control.cycle == 0 and brain.control.state == "idle"
    assert brain.db.one("SELECT COUNT(*) c FROM prompt_versions WHERE role='executor'")["c"] == 1
    # the sandbox container is recreated on demand and is clean
    r = await brain.sandbox.shell("ls /workspace/tools | wc -l")
    assert r.stdout.strip() == "0"


def test_reset_endpoint_requires_confirmation(settings):
    settings.autostart = False
    with TestClient(create_app(Brain(settings))) as c:
        assert c.post("/api/v1/reset", json={"confirm": "no"}).status_code == 400
        assert c.post("/api/v1/reset").status_code == 422
        assert c.post("/api/v1/reset", json={"confirm": "RESET"}).json()["state"] == "idle"
        s = c.get("/api/v1/state").json()
        assert len(s["goals"]) == 1 and s["lessons"] == [] and s["journal"] == []
