import json
import re
import time

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from brain.agents import Agent
from brain.api import create_app
from brain.core import Brain
from brain.tools import ToolContext
from brain.wiki import SPECIAL

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def wb(brain):
    """A brain with its root goal and an initialised wiki, embeddings off (vector tests switch them on explicitly)."""
    brain.settings.embeddings = "off"
    await brain._bootstrap()
    await brain.wiki.init()
    return brain


def _insert_tool(b, name="sum_numbers"):
    b.db.execute(
        "INSERT INTO tools(name,description,params,status,created,calls,failures,test_output) VALUES(?,?,?,?,?,?,?,?)",
        (name, "adds two numbers", json.dumps({"a": "int", "b": "int"}), "active", time.time(), 3, 0, "2 passed"),
    )


async def test_init_creates_schema_log_index_and_status(wb):
    d = wb.wiki.dir
    assert {p.stem for p in d.glob("*.md")} >= {"SCHEMA", "log", "index", "lint", "status", "lessons", "evolution", "self-model"}
    assert "Ingest" in (d / "SCHEMA.md").read_text()
    assert "[[status]]" in (d / "index.md").read_text()
    meta = (d / "status.md").read_text()
    assert meta.startswith("---\ntitle: Project status\ntype: meta") and "managed: auto" in meta


async def test_page_roundtrip_links_backlinks_and_path_safety(wb):
    w = wb.wiki
    assert w.write_page("concepts/sqlite", "SQLite", "concept", "Embedded database", "Used by [[Brain Core]] for everything.", sources=["memory:1"], managed="llm")
    assert w.write_page("concepts/brain-core", "Brain Core", "concept", "The core", "Reads from [[concepts/sqlite]] and [[status]].", managed="llm")
    assert not w.write_page("concepts/sqlite", "SQLite", "concept", "Embedded database", "Used by [[Brain Core]] for everything.", sources=["memory:1"], managed="llm")  # unchanged
    page = w.read_page("concepts/sqlite")
    assert page["sources"] == ["memory:1"] and page["links"] == [{"target": "Brain Core", "id": "concepts/brain-core", "title": "Brain Core"}]
    assert [b["id"] for b in w.read_page("concepts/brain-core")["backlinks"]] == ["concepts/sqlite"]
    assert [b["id"] for b in w.read_page("concepts/sqlite")["backlinks"]] == ["concepts/brain-core"]
    for bad in ("../secrets", "/etc/passwd", "a/b/c", "Concepts/X", ""):
        assert w.read_page(bad) is None
        with pytest.raises(ValueError):
            w.write_page(bad or "x y", "t", "note", "s", "b")


async def test_generated_pages_log_and_idempotence(wb):
    b, w = wb, wb.wiki
    root = b.goals.root()["id"]
    ok = await b.goals.add("Create the sum tool", "a sum is needed", root, 0.8, 0.7)
    ko = await b.goals.add("Read a nonexistent site", "wrong url", root, 0.5, 0.4)
    await b.goals.set_status(ok, "done", "sum tool created and tested")
    await b.goals.set_status(ko, "failed", "DNS does not resolve")
    _insert_tool(b)
    await b.lessons.add("Always check that the URL exists before downloading it", "fix")
    ev = await b.bus.publish("goal.update", None, **b.goals.get(ok))
    w._on_event(ev)
    w._on_event(await b.bus.publish("tool.created", "exec-1", name="sum_numbers", description="adds two numbers", passed=True))

    assert await w.sync_all() > 0
    d = w.dir
    ep = (d / f"episodes/goal-{ok}.md").read_text()
    assert "succeeded" in ep and "sum tool created and tested" in ep and "[[status]]" in ep
    assert "failed" in (d / f"episodes/goal-{ko}.md").read_text()
    assert "sum_numbers" in (d / "tools/sum_numbers.md").read_text()
    assert "URL exists" in (d / "lessons.md").read_text()
    status, index = (d / "status.md").read_text(), (d / "index.md").read_text()
    assert f"[[episodes/goal-{ok}]]" in status and "[[tools/sum_numbers]]" in status
    assert f"[[episodes/goal-{ko}]]" in index and "## Tools created (1)" in index

    log = (d / "log.md").read_text()
    entries = re.findall(r"^## \[\d{4}-\d{2}-\d{2} \d{2}:\d{2}\] (\w+) \| (.+)$", log, re.M)
    assert ("goal", f"#{ok} Create the sum tool") in entries and ("tool", "sum_numbers (tests OK)") in entries
    # a second sync changes nothing and does not duplicate log lines
    w._on_event(ev)
    assert await w.sync_all() == 0
    assert (d / "log.md").read_text() == log


async def test_scan_picks_up_manual_edits_and_deletions(wb):
    w = wb.wiki
    (w.dir / "concepts").mkdir(exist_ok=True)
    (w.dir / "concepts/my-idea.md").write_text("# My idea\n\nWritten by hand in Obsidian, linked to [[status]].\n")
    assert w.scan() == 1
    row = wb.db.one("SELECT title,type,managed,links FROM wiki_pages WHERE id='concepts/my-idea'")
    assert row["title"] == "My idea" and row["managed"] == "user" and json.loads(row["links"]) == ["status"]
    assert w.scan() == 0
    (w.dir / "concepts/my-idea.md").unlink()
    assert w.scan() == 1 and wb.db.one("SELECT id FROM wiki_pages WHERE id='concepts/my-idea'") is None


async def test_lint_finds_broken_links_orphans_contradictions(wb):
    w = wb.wiki
    w.write_page("concepts/one", "One", "concept", "first", "Refers to [[Missing Page]] and [[concepts/two]].", managed="llm")
    w.write_page("concepts/two", "Two", "concept", "second", "⚠ Contradiction: it used to say A, now B.", managed="llm")
    w.write_page("concepts/alone", "Alone", "concept", "nobody cites me", "Isolated page.", managed="llm")
    rep = w.lint()
    assert rep["broken"] == [{"target": "Missing Page", "from": ["concepts/one"]}]
    assert rep["orphans"] == ["concepts/alone", "concepts/one"]
    assert rep["contradictions"] == ["concepts/two"] and rep["total"] == 4
    assert "Missing Page" in (w.dir / "lint.md").read_text()
    assert wb.db.kv_get("wiki_wanted")[0]["target"] == "Missing Page"


async def test_keyword_search_without_vectors_and_note_filing(wb):
    w = wb.wiki
    w.write_page("concepts/podman", "Podman sandbox", "concept", "Container to run the tools", "The tools run in a rootless Podman container.", managed="llm")
    w.write_page("concepts/lm-studio", "LM Studio", "concept", "Local model server", "Serves the Qwen model through an OpenAI-compatible API.", managed="llm")
    hits = await w.search("where do the tools run in the container?")
    assert hits[0]["id"] == "concepts/podman"
    assert await w.search("zxqwv nonexistent") == []
    pid = await w.note("Why Podman and not Docker", "Podman is rootless and needs no daemon: safer for code written by the agents.", source="agent:exec-1")
    assert pid == "notes/why-podman-and-not-docker"
    assert (await w.search("daemon rootless safer"))[0]["id"] == pid
    assert "## [" in (w.dir / "log.md").read_text() and "query | Filed" in (w.dir / "log.md").read_text()
    with pytest.raises(ValueError):
        await w.note("x", "short")


async def test_agent_tools_wiki_search_read_note(wb):
    w = wb.wiki
    w.write_page("insights/cache", "Tool cache", "insight", "pytest tools should be cached", "Re-running the tests costs 20 seconds.", managed="llm")
    ctx = ToolContext(wb, Agent(wb, "executor", "trial"))
    hits = await wb.tools.call(ctx, "wiki_search", {"query": "test pytest cache"})
    assert hits[0]["id"] == "insights/cache"
    page = await wb.tools.call(ctx, "wiki_read", {"id": "insights/cache"})
    assert "20 seconds" in page["body"]
    assert "not found" in await wb.tools.call(ctx, "wiki_read", {"id": "insights/nope"})
    assert "filed as [[notes/" in await wb.tools.call(ctx, "wiki_note", {"title": "Discovery about tests", "body": "With -x the tests stop at the first error."})


async def test_vectors_semantic_search_and_3d_map_with_real_embedding_model(wb):
    w = wb.wiki
    pages = [
        ("concepts/cooking", "Italian cooking", "Pasta is cooked in boiling salted water with a drizzle of oil."),
        ("concepts/astronomy", "Astronomy", "Planets orbit around stars and the moon revolves around the earth."),
        ("concepts/database", "Database", "Relational tables use indexes and primary keys for queries."),
        ("concepts/football", "Football", "The team scores a goal in the stadium in front of the fans."),
    ]
    for pid, title, body in pages:
        w.write_page(pid, title, "concept", body[:60], body + " See [[status]].", managed="llm")
    res = await w.embed_pending(force=True)
    assert res["available"] is True and res["embedded"] >= 4, res
    assert (await w.stats())["embedded"] == (await w.stats())["pages"]
    wb.settings.embeddings = "on"
    hits = await w.search("celestial objects traveling through space")
    assert hits[0]["id"] == "concepts/astronomy", hits
    g = w.graph()
    assert len(g["nodes"]) >= 8 and all(n["embedded"] for n in g["nodes"])
    assert all(-1.0 <= n[k] <= 1.0 for n in g["nodes"] for k in ("sx", "sy", "sz"))
    assert len({(n["sx"], n["sy"], n["sz"]) for n in g["nodes"]}) == len(g["nodes"])  # distinct positions
    assert {"source": "concepts/cooking", "target": "status"} in g["edges"]


async def test_ingest_with_real_llm_creates_linked_pages_and_advances_cursor(wb):
    w = wb.wiki
    await wb.memory.add("fact", "Brain stores memories, journal and goals in a local SQLite database called brain.db", ["db"], 0.8)
    await wb.memory.add("insight", "Parallel requests to LM Studio are serialised: more agents do not speed up generation", ["llm"], 0.9)
    await wb.memory.add("user", "Lorenzo said: the chat must have priority over the agents", ["user"], 0.9)
    wb.memory.journal_add("outcome", "#2 Chat priority: DONE - agent requests are interrupted when a message from Lorenzo arrives")
    assert w.pending_items() == 4
    out = await w.ingest()
    assert out["items"] == 4 and out["pages"] >= 1 and "error" not in out, out
    assert w.pending_items() == 0
    rows = wb.db.query("SELECT id,type,managed,summary,links FROM wiki_pages WHERE managed='llm'")
    assert rows and all(r["id"].split("/")[0] in {"concepts", "entities", "insights", "decisions", "phases"} for r in rows)
    assert all(r["summary"] for r in rows)
    text = (w.dir / f"{rows[0]['id']}.md").read_text()
    assert text.startswith("---\n") and "managed: llm" in text and re.search(r"sources: \[(memory|journal):\d+", text)
    log = (w.dir / "log.md").read_text()
    assert re.search(r"^## \[[\d\- :]+\] ingest \| \d+ pages from 3 memories and 1 journal entries", log, re.M)
    assert await w.ingest() == {"pages": 0, "items": 0}  # nothing new


async def test_semantic_map_has_three_distinct_normalised_coordinates(wb):
    w = wb.wiki
    vecs = {"concepts/a": [1, 0, 0, 0, 0], "concepts/b": [0, 1, 0, 0, 0], "concepts/c": [0, 0, 1, 0, 0], "concepts/d": [1, 1, 1, 1, 1]}
    for pid, v in vecs.items():
        w.write_page(pid, pid, "concept", pid, f"Page {pid} linked to [[status]].", managed="llm")
        wb.db.execute("UPDATE wiki_pages SET embedding=? WHERE id=?", (json.dumps(v), pid))
    nodes = {n["id"]: n for n in w.graph()["nodes"]}
    pts = [(nodes[p]["sx"], nodes[p]["sy"], nodes[p]["sz"]) for p in vecs]
    assert all(-1.0 <= c <= 1.0 for pt in pts for c in pt) and len(set(pts)) == 4
    assert nodes["status"]["sx"] is None  # pages without a vector float by their links


async def test_ingest_page_validation_sources_and_user_pages_are_protected(wb):
    w = wb.wiki
    pid = w._ingest_page({"id": "concepts/x y", "title": "Valid idea", "type": "insight", "summary": "s", "sources": ["memory:4", "<script>"],
                          "body": "See [[memory:4]] and [[status]]: a page with enough text to pass."})
    assert pid == "insights/valid-idea"  # bad id falls back to type folder + slug
    page = w.read_page(pid)
    assert page["sources"] == ["memory:4"] and "[[memory:4]]" not in page["body"] and "(memory:4)" in page["body"]
    assert w._ingest_page({"title": "ab", "body": "short"}) is None
    (w.dir / "concepts").mkdir(exist_ok=True)
    (w.dir / "concepts/mine.md").write_text("# Mine\n\nLorenzo's own page, never to be touched.\n")
    w.scan()
    assert w._ingest_page({"id": "concepts/mine", "title": "Mine", "body": "The librarian tries to rewrite it with long enough text."}) is None
    assert "never to be touched" in (w.dir / "concepts/mine.md").read_text()
    assert w._ingest_page({"id": "episodes/goal-1", "title": "Fake", "type": "concept", "body": "Tries to invade a folder managed by the database."}) == "concepts/fake"


async def test_reset_wipes_the_wiki(wb):
    w = wb.wiki
    await w.note("To forget", "This note must disappear with the factory reset.")
    assert (w.dir / "notes/to-forget.md").exists()
    wb.db.wipe()
    await w.reset()
    assert not list((w.dir / "notes").glob("*.md"))
    assert wb.db.one("SELECT id FROM wiki_pages WHERE id='notes/to-forget'") is None
    assert {s for s in SPECIAL} <= {p.stem for p in w.dir.glob("*.md")}


def test_wiki_endpoints(settings):
    settings.autostart = False
    settings.embeddings = "off"
    with TestClient(create_app(Brain(settings))) as c:
        st = c.get("/api/v1/wiki").json()
        assert st["pages"] >= 4 and st["embeddings"]["mode"] == "off" and st["embeddings"]["available"] is False
        g = c.get("/api/v1/wiki/graph").json()
        assert "status" in {n["id"] for n in g["nodes"]} and {"source", "target"} <= set(g["edges"][0])
        page = c.get("/api/v1/wiki/page", params={"id": "status"}).json()
        assert page["title"] == "Project status" and page["links"]
        assert "# Index" in c.get("/api/v1/wiki/page", params={"id": "index"}).json()["body"]
        assert c.get("/api/v1/wiki/page", params={"id": "nope/x"}).status_code == 404
        assert c.get("/api/v1/wiki/page", params={"id": "../../etc/passwd"}).status_code == 404
        hits = c.get("/api/v1/wiki/search", params={"q": "project status goals"}).json()
        assert hits and hits[0]["id"] == "status"
