import pytest
from fastapi.testclient import TestClient

from brain.agents import Agent
from brain.api import create_app
from brain.core import Brain
from brain.projects import ProjectError, ProjectNotFound
from brain.prompts import DEFAULTS
from brain.tools import ToolContext

TOOL = "def run(x):\n    return {'y': x * 2}\n"
TEST = "from dbl import run\n\ndef test_run():\n    assert run(2) == {'y': 4}\n"
PROMPT = "You are an experimental executor tuned for project Alpha. " * 6


async def _populate(brain, tag: str):
    """Put recognisable state in every layer a snapshot must cover."""
    ctx = ToolContext(brain, Agent(brain, "engineer", "t"))
    root = brain.goals.root()["id"]
    await brain.goals.add(f"goal {tag}", "", root)
    await brain.memory.add("fact", f"memory {tag}", [tag])
    brain.memory.journal_add("reflection", f"journal {tag}")
    await brain.lessons.add(f"lesson {tag} use relative paths", "fix")
    await brain.wiki.init()
    await brain.wiki.note(f"note {tag}", f"wiki body for the {tag} project, long enough")
    await brain.tools.call(ctx, "write_file", {"path": f"notes/{tag}.txt", "content": tag})
    brain.control.cycle = 9


async def test_save_then_load_restores_every_layer(brain):
    brain.settings.autostart = False
    await brain._bootstrap()
    await _populate(brain, "alpha")
    meta = await brain.projects.save("Alpha")
    assert meta["id"] == "alpha" and meta["cycle"] == 9 and meta["goals"]["total"] == 1 and meta["memories"] >= 1
    assert brain.projects.current() == {"name": "Alpha", "id": "alpha", "saved": True, "saved_at": meta["saved_at"]}

    # diverge: new goal/memory, edited workspace, evolved prompt
    root = brain.goals.root()["id"]
    await brain.goals.add("goal beta", "", root)
    await brain.memory.add("fact", "memory beta", ["beta"])
    await brain.wiki.note("note beta", "wiki body for the beta project, long enough")
    await brain.tools.call(ToolContext(brain, Agent(brain, "engineer", "t")), "write_file", {"path": "notes/beta.txt", "content": "b"})
    assert (await brain.evolution.propose_prompt("executor", PROMPT, "test"))["ok"]
    brain.control.cycle = 30

    loaded = await brain.projects.load("alpha", save_current=False)

    assert loaded["name"] == "Alpha"
    assert [g["title"] for g in brain.goals.all() if g["parent_id"]] == ["goal alpha"]
    assert {m["text"] for m in brain.memory.list(None, 50, 0)} >= {"memory alpha"}
    assert not any("beta" in m["text"] for m in brain.memory.list(None, 50, 0))
    ws = brain.settings.workspace_dir
    assert (ws / "notes" / "alpha.txt").read_text() == "alpha" and not (ws / "notes" / "beta.txt").exists()
    assert brain.evolution.prompt("executor") == DEFAULTS["executor"]
    assert brain.wiki.read_page("notes/note-alpha") and brain.wiki.read_page("notes/note-beta") is None
    assert brain.control.cycle == 9 and brain.control.state == "idle"
    assert brain.projects.current_name() == "Alpha"


async def test_new_project_starts_clean_and_keeps_the_previous_one(brain):
    brain.settings.autostart = False
    await brain._bootstrap()
    await _populate(brain, "alpha")
    await brain.projects.save("Alpha")

    meta = await brain.projects.new("Beta", main_goal="Learn to cook a perfect risotto", save_current=True)

    assert meta["id"] == "beta" and brain.projects.current_name() == "Beta"
    assert brain.memory.count() >= 1  # only the memory written by set_main_goal
    assert not any("alpha" in m["text"] for m in brain.memory.list(None, 50, 0))
    assert "risotto" in brain.goals.root()["description"]
    assert brain.control.cycle == 0
    ids = {p["id"]: p for p in brain.projects.list()}
    assert set(ids) == {"alpha", "beta"} and ids["beta"]["current"] and not ids["alpha"]["current"]
    assert "risotto" in ids["beta"]["main_goal"]

    with pytest.raises(ProjectError):
        await brain.projects.new("beta", save_current=False)
    with pytest.raises(ProjectError):
        await brain.projects.new("   ")

    # the old project is intact and can be loaded back (saving Beta first)
    await brain.projects.load("alpha", save_current=True)
    assert any("alpha" in m["text"] for m in brain.memory.list(None, 50, 0))
    assert not any("risotto" in m["text"] for m in brain.memory.list(None, 50, 0))
    assert {p["id"] for p in brain.projects.list()} == {"alpha", "beta"}


async def test_delete_and_id_validation(brain):
    await brain._bootstrap()
    await brain.projects.save("Gamma Project")
    assert [p["id"] for p in brain.projects.list()] == ["gamma-project"]
    for bad in ("../etc", "..", "gamma-project/../../x", "nope", ""):
        with pytest.raises(ProjectNotFound):
            brain.projects.delete(bad)
        with pytest.raises(ProjectNotFound):
            await brain.projects.load(bad)
    brain.projects.delete("gamma-project")
    assert brain.projects.list() == [] and not brain.projects.current()["saved"]


def test_projects_endpoints(settings):
    settings.autostart = False
    with TestClient(create_app(Brain(settings))) as c:
        r = c.get("/api/v1/projects").json()
        assert r["current"]["name"] == "Untitled project" and r["items"] == []
        assert c.get("/api/v1/state").json()["project"]["name"] == "Untitled project"

        saved = c.post("/api/v1/projects/save", json={"name": "Demo run"}).json()
        assert saved["id"] == "demo-run"
        assert c.post("/api/v1/projects/new", json={"name": "demo run"}).status_code == 422
        assert c.post("/api/v1/projects/new", json={"name": ""}).status_code == 422

        new = c.post("/api/v1/projects/new", json={"name": "Second", "main_goal": "Study the moons of Jupiter"}).json()
        assert new["id"] == "second"
        s = c.get("/api/v1/state").json()
        assert s["project"]["name"] == "Second" and "Jupiter" in s["goals"][0]["description"]

        assert c.post("/api/v1/projects/demo-run/load", json={"save_current": False}).json()["name"] == "Demo run"
        s = c.get("/api/v1/state").json()
        assert s["project"]["name"] == "Demo run" and "Jupiter" not in s["goals"][0]["description"]
        assert c.post("/api/v1/projects/missing/load").status_code == 404

        assert c.delete("/api/v1/projects/second").json() == {"deleted": "second"}
        assert c.delete("/api/v1/projects/second").status_code == 404
        assert [p["id"] for p in c.get("/api/v1/projects").json()["items"]] == ["demo-run"]
