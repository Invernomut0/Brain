import pytest
from fastapi.testclient import TestClient

from brain.api import create_app
from brain.core import Brain
from brain.selfmodel import DEFAULT_MODEL


async def test_owner_is_unknown_until_given_and_flows_into_the_self_model(brain):
    assert brain.owner_name == "" and brain.owner == "the owner"
    assert "Lorenzo" not in brain.selfmodel.render() and "owner" in brain.selfmodel.get()["about_user"]

    assert await brain.set_owner("  Maria   Rossi ") == "Maria Rossi"
    assert brain.owner == "Maria Rossi"
    assert brain.selfmodel.get()["about_user"].startswith("Maria Rossi is my creator")

    await brain.set_owner("Giulia")  # renaming replaces the old name instead of stacking
    assert brain.selfmodel.get()["about_user"].startswith("Giulia is my creator") and "Maria" not in brain.selfmodel.get()["about_user"]

    with pytest.raises(ValueError):
        await brain.set_owner("   ")


async def test_a_customised_about_user_is_not_overwritten(brain):
    await brain.selfmodel.update({"about_user": "My owner runs a bakery and likes short answers."})
    await brain.set_owner("Anna")
    assert brain.selfmodel.get()["about_user"] == "My owner runs a bakery and likes short answers."


async def test_owner_name_is_used_in_ask_user_and_survives_save_and_load(brain):
    from brain.agents import Agent
    from brain.tools import ToolContext

    await brain._bootstrap()
    await brain.set_owner("Marco")
    ctx = ToolContext(brain, Agent(brain, "executor", "t"))
    out = await brain.tools.call(ctx, "ask_user", {"message": "which color?", "wait": 0})
    assert "Marco has not replied" in str(out)

    await brain.projects.save("Owned")
    await brain.reset()
    assert brain.owner_name == ""  # factory state asks again
    await brain.projects.load("owned", save_current=False)
    assert brain.owner_name == "Marco"


def test_owner_endpoints(settings):
    settings.autostart = False
    with TestClient(create_app(Brain(settings))) as c:
        assert c.get("/api/v1/owner").json() == {"name": ""}
        assert c.get("/api/v1/state").json()["owner"] == ""
        assert c.put("/api/v1/owner", json={"name": " "}).status_code == 422
        assert c.put("/api/v1/owner", json={"name": "Elena"}).json() == {"name": "Elena"}
        assert c.get("/api/v1/state").json()["owner"] == "Elena"
        # a new project may carry the owner over
        c.post("/api/v1/projects/new", json={"name": "P2", "owner": "Elena"})
        assert c.get("/api/v1/owner").json() == {"name": "Elena"}
        assert DEFAULT_MODEL["about_user"] != c.get("/api/v1/state").json()["selfmodel"]["about_user"]
