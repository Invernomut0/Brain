import pytest
from fastapi.testclient import TestClient

from brain.api import create_app
from brain.core import Brain
from brain.names import ROLE_FLAVORS, TOKEN_RE, AgentNameError, MAX_NAME
from brain.names_data import DICTIONARY, PATTERNS


def test_every_pattern_token_exists_and_every_flavor_is_a_style():
    for style, patterns in PATTERNS.items():
        for p in patterns:
            assert all(t in DICTIONARY and DICTIONARY[t] for t in TOKEN_RE.findall(p)), (style, p)
    assert all(s in PATTERNS for styles in ROLE_FLAVORS.values() for s in styles)
    assert all(r in ROLE_FLAVORS for r in ("planner", "executor", "researcher", "engineer", "critic", "reflector", "evolver"))


async def test_generated_names_fit_the_limit_in_every_style(brain):
    for style in PATTERNS:
        brain.names.set_style(style)
        for i in range(150):
            name = brain.names.assign(f"{style}-{i}", "executor")
            assert 1 <= len(name) <= MAX_NAME and name.isprintable()


async def test_every_agent_gets_a_stable_unique_name(brain):
    names = {}
    for i in range(300):
        aid = f"critic-{i}"
        names[aid] = brain.names.assign(aid, "critic")
    assert all(names.values()) and len(set(n.lower() for n in names.values())) == 300
    assert brain.names.assign("critic-5", "critic") == names["critic-5"]  # same agent, same name
    assert brain.names.get("critic-5")["style"] in ROLE_FLAVORS["critic"]


async def test_style_is_selectable_and_off_keeps_plain_ids(brain):
    assert brain.names.style() == "all"
    for style in PATTERNS:
        brain.names.set_style(style)
        assert brain.names.assign(f"x-{style}", "executor")
        assert brain.names.get(f"x-{style}")["style"] == style
    brain.names.set_style("off")
    assert brain.names.assign("new-agent", "executor") is None
    with pytest.raises(AgentNameError):
        brain.names.set_style("no-such-style")


async def test_rename_by_hand_and_reroll(brain):
    brain.names.assign("eng-1", "engineer")
    brain.names.assign("eng-2", "engineer")
    assert brain.names.rename("eng-1", "  Captain   Nemo ") == "Captain Nemo"
    assert brain.names.get("eng-1")["style"] == "custom"
    with pytest.raises(AgentNameError):
        brain.names.rename("eng-2", "captain nemo")  # unique, case-insensitive
    with pytest.raises(AgentNameError):
        brain.names.rename("eng-2", "x" * 49)
    with pytest.raises(AgentNameError):
        brain.names.rename("eng-2", "   ")
    assert brain.names.rename("eng-1", "Captain Nemo") == "Captain Nemo"  # keeping its own name is fine
    rolled = brain.names.reroll("eng-1")
    assert rolled != "Captain Nemo"
    assert brain.names.get("eng-1")["name"] == rolled


async def test_spawn_event_and_snapshot_carry_the_name(brain):
    ev = await brain.bus.publish("agent.spawn", "researcher-ab12", role="researcher", parent=None, goal_id=None, task="t")
    assert ev.data["name"] == brain.names.get("researcher-ab12")["name"]
    assert brain.snapshot()["names"]["researcher-ab12"] == ev.data["name"]
    assert brain.snapshot()["naming"]["style"] == "all"
    brain.names.set_style("off")
    ev = await brain.bus.publish("agent.spawn", "researcher-cd34", role="researcher", parent=None, goal_id=None, task="t")
    assert "name" not in ev.data


async def test_reset_forgets_names(brain):
    brain.settings.autostart = False
    brain.names.assign("a-1", "planner")
    await brain.reset()
    assert brain.names.all() == {}


def test_naming_endpoints(settings):
    settings.autostart = False
    with TestClient(create_app(Brain(settings))) as c:
        assert c.get("/api/v1/naming").json()["style"] == "all"
        assert c.put("/api/v1/naming", json={"style": "pirate"}).json()["style"] == "pirate"
        assert c.put("/api/v1/naming", json={"style": "x"}).status_code == 422

        brain = c.app.state.brain
        brain.names.assign("planner", "planner")
        assert c.put("/api/v1/agents/planner/name", json={"name": "Ada"}).json() == {"agent_id": "planner", "name": "Ada"}
        assert c.get("/api/v1/state").json()["names"]["planner"] == "Ada"
        rolled = c.put("/api/v1/agents/planner/name", json={}).json()["name"]
        assert rolled and rolled != ""
        brain.names.assign("other", "critic")
        assert c.put("/api/v1/agents/other/name", json={"name": rolled}).status_code == 422
        assert c.put("/api/v1/agents/ghost/name", json={"name": "Boo"}).status_code == 404
