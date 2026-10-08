import pytest
from fastapi.testclient import TestClient

from brain.api import create_app
from brain.core import Brain


async def _seed(brain):
    await brain.memory.add("fact", "Il progetto Brain usa SQLite per la persistenza dei ricordi", ["db"], 0.7)
    await brain.memory.add("insight", "Le richieste parallele a LM Studio vengono serializzate", ["llm"], 0.8)
    await brain.memory.add("user", "Lorenzo ha detto: voglio un tab con i ricordi", ["user"], 0.9)


async def test_memory_list_kinds_and_filter(brain):
    await _seed(brain)
    assert brain.memory.kinds() == {"fact": 1, "insight": 1, "user": 1}
    items = brain.memory.list()
    assert [i["kind"] for i in items] == ["user", "insight", "fact"]  # newest first
    assert "embedding" not in items[0] and items[0]["tags"] == ["user"]
    assert [i["kind"] for i in brain.memory.list("insight")] == ["insight"]
    assert len(brain.memory.list(limit=2)) == 2 and len(brain.memory.list(limit=2, offset=2)) == 1


async def test_memory_find_ranks_relevant_and_drops_unrelated(brain):
    await _seed(brain)
    hits = await brain.memory.find("SQLite persistenza")
    assert hits and hits[0]["kind"] == "fact"
    assert all("sqlite" in h["text"].lower() or h["score"] >= 0.45 for h in hits)
    assert await brain.memory.find("zxqwv inesistente") == []
    assert await brain.memory.find("SQLite", kind="insight") == []


def test_memories_endpoint(settings):
    settings.autostart = False
    brain = Brain(settings)
    for kind, text in [("fact", "Python 3.12 gira nella sandbox Podman"), ("user", "Lorenzo preferisce risposte brevi")]:
        brain.db.execute("INSERT INTO memories(ts,kind,text,tags,importance) VALUES(1,?,?,?,0.5)", (kind, text, "[]"))
    with TestClient(create_app(brain)) as c:
        r = c.get("/api/v1/memories").json()
        assert r["total"] == 2 and r["kinds"] == {"fact": 1, "user": 1} and len(r["items"]) == 2
        assert [i["kind"] for i in c.get("/api/v1/memories", params={"kind": "user"}).json()["items"]] == ["user"]
        found = c.get("/api/v1/memories", params={"q": "sandbox Podman"}).json()["items"]
        assert found and "Podman" in found[0]["text"]


async def test_status_fallback_is_deterministic_and_has_five_lines(brain):
    f = brain.status.facts()
    r = brain.status._fallback(f)
    assert len(r["lines"]) == 5 and r["source"] == "proxy" and 0 <= r["progress"] <= 100


async def test_status_report_with_real_model_then_stale_after_new_work(brain):
    await brain._bootstrap()  # creates the root goal
    s = brain.status.current()
    assert s["report"] is None and s["stale"] and s["facts"]["goals"]["total"] == 0
    gid = await brain.goals.add("Creare un tool di somma", "scrivi un tool", brain.goals.root()["id"])
    await brain.goals.set_status(gid, "done", "tool creato e testato")
    out = await brain.status.refresh()
    rep = out["report"]
    assert len(rep["lines"]) == 5 and all(rep["lines"]) and 0 <= rep["progress"] <= 100
    assert rep["source"] in ("llm", "proxy") and not out["stale"]
    assert out["facts"]["goals"]["done"] == 1 and out["facts"]["recent_done"][0]["title"].startswith("Creare")
    assert not brain.status.current()["stale"]
    await brain.goals.add("Altro obiettivo", "", brain.goals.root()["id"])
    assert brain.status.current()["stale"]
