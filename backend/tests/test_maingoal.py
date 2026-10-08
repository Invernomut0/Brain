import pytest
from fastapi.testclient import TestClient

from brain.api import create_app
from brain.config import ROOT_GOAL
from brain.core import Brain

NEW_GOAL = "Imparare a comporre musica generativa e pubblicare un brano ogni settimana."


async def test_main_goal_can_be_changed_and_cancels_the_old_queue(brain):
    await brain._bootstrap()
    assert brain.orchestrator.main_goal() == ROOT_GOAL
    root = brain.goals.root()["id"]
    pending = [await brain.goals.add(f"g{i}", "", root) for i in range(2)]
    done = await brain.goals.add("fatto", "", root)
    await brain.goals.set_status(done, "done", "ok")

    res = await brain.set_main_goal(NEW_GOAL, archive_pending=True)

    assert res["cancelled"] == 2
    assert brain.orchestrator.main_goal() == NEW_GOAL
    assert brain.goals.root()["title"] == NEW_GOAL
    assert [brain.goals.get(g)["status"] for g in pending] == ["cancelled", "cancelled"]
    assert brain.goals.get(done)["status"] == "done"
    assert brain.goals.pending() == []
    assert brain.selfmodel.get()["purpose"] == NEW_GOAL
    assert any("obiettivo principale" in j["text"] for j in brain.memory.journal_recent())


async def test_changing_the_goal_can_keep_the_queue(brain):
    await brain._bootstrap()
    gid = await brain.goals.add("g", "", brain.goals.root()["id"])
    assert (await brain.set_main_goal(NEW_GOAL, archive_pending=False))["cancelled"] == 0
    assert brain.goals.get(gid)["status"] == "pending"


async def test_too_short_goal_is_rejected(brain):
    await brain._bootstrap()
    with pytest.raises(ValueError):
        await brain.set_main_goal("troppo", True)


async def test_legacy_planner_prompt_is_migrated_to_the_editable_goal(brain):
    p = brain.settings.evolvable_dir / "prompts" / "planner.md"
    p.write_text("Sei il Planner di Brain, un sistema autonomo il cui obiettivo radice e' evolvere verso l'autocoscienza. Altro testo.")
    await brain.evolution.seed()
    text = p.read_text()
    assert "autocoscienza" not in text and "OBIETTIVO RADICE scelto da Lorenzo" in text and "Altro testo." in text


def test_api_main_goal_and_no_autostart(settings):
    app = create_app(Brain(settings))  # default settings: the loop must not start by itself
    with TestClient(app) as c:
        assert c.get("/api/v1/state").json()["control"]["state"] == "idle"
        assert c.get("/api/v1/main-goal").json()["text"] == ROOT_GOAL
        assert c.put("/api/v1/main-goal", json={"text": "corto"}).status_code == 422
        r = c.put("/api/v1/main-goal", json={"text": NEW_GOAL, "archive_pending": True})
        assert r.status_code == 200 and r.json()["text"] == NEW_GOAL
        goals = c.get("/api/v1/state").json()["goals"]
        assert [g["description"] for g in goals if g["parent_id"] is None] == [NEW_GOAL]
        assert c.get("/api/v1/state").json()["control"]["state"] == "idle"
