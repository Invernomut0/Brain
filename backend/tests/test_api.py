import pytest
from fastapi.testclient import TestClient

from brain.api import create_app
from brain.core import Brain


@pytest.fixture
def client(settings):
    settings.autostart = False  # the idle-state assertions need a stopped system
    app = create_app(Brain(settings))
    with TestClient(app) as c:
        yield c


def test_state_has_root_goal_and_tools(client):
    s = client.get("/api/v1/state").json()
    assert s["goals"][0]["parent_id"] is None
    assert {"create_tool", "web_search", "python_exec"} <= {t["name"] for t in s["tools"]}
    assert s["control"]["state"] == "idle"


def test_control_lifecycle_and_kill(client):
    assert client.post("/api/v1/control/start").json()["state"] == "running"
    assert client.post("/api/v1/control/pause").json()["state"] == "paused"
    assert client.post("/api/v1/control/resume").json()["state"] == "running"
    assert client.post("/api/v1/control/kill").json()["state"] == "killed"
    assert client.post("/api/v1/control/bogus").status_code == 404


def test_unknown_api_path_is_404_not_the_spa_page(client):
    assert client.get("/api/v1/nonexistent").status_code == 404


def test_budget_and_chat_validation(client):
    assert client.post("/api/v1/budget", json={"max_cycles": 3}).json()["max_cycles"] == 3
    assert client.post("/api/v1/chat", json={"text": "  "}).status_code == 422


def test_websocket_streams_snapshot_then_events(client):
    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "snapshot"
        client.post("/api/v1/chat", json={"text": "hi Brain, who are you?"})
        seen = set()
        for _ in range(40):
            seen.add(ws.receive_json()["type"])
            if "chat.message" in seen and "llm.end" in seen:
                break
        assert "chat.message" in seen


def test_dashboard_serves_avatars_but_never_files_outside_dist(client):
    from brain.api import DIST

    if not (DIST / "avatars").exists():
        pytest.skip("dashboard not built")
    r = client.get("/avatars/engineer.png")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    # %2e%2e escapes the dist folder: the repo .env.example must not be served
    leak = client.get("/%2e%2e/%2e%2e/.env.example")
    assert "BRAIN_" not in leak.text
