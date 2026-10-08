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


def _insert_tool(b, name="somma_numeri"):
    b.db.execute(
        "INSERT INTO tools(name,description,params,status,created,calls,failures,test_output) VALUES(?,?,?,?,?,?,?,?)",
        (name, "somma due numeri", json.dumps({"a": "int", "b": "int"}), "active", time.time(), 3, 0, "2 passed"),
    )


async def test_init_creates_schema_log_index_and_status(wb):
    d = wb.wiki.dir
    assert {p.stem for p in d.glob("*.md")} >= {"SCHEMA", "log", "index", "lint", "status", "lessons", "evolution", "self-model"}
    assert "Ingest" in (d / "SCHEMA.md").read_text()
    assert "[[status]]" in (d / "index.md").read_text()
    meta = (d / "status.md").read_text()
    assert meta.startswith("---\ntitle: Stato del progetto\ntype: meta") and "managed: auto" in meta


async def test_page_roundtrip_links_backlinks_and_path_safety(wb):
    w = wb.wiki
    assert w.write_page("concepts/sqlite", "SQLite", "concept", "Database embedded", "Usato da [[Brain Core]] per tutto.", sources=["memory:1"], managed="llm")
    assert w.write_page("concepts/brain-core", "Brain Core", "concept", "Il nucleo", "Legge da [[concepts/sqlite]] e [[status]].", managed="llm")
    assert not w.write_page("concepts/sqlite", "SQLite", "concept", "Database embedded", "Usato da [[Brain Core]] per tutto.", sources=["memory:1"], managed="llm")  # unchanged
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
    ok = await b.goals.add("Creare il tool somma", "serve una somma", root, 0.8, 0.7)
    ko = await b.goals.add("Leggere un sito inesistente", "url sbagliato", root, 0.5, 0.4)
    await b.goals.set_status(ok, "done", "tool somma creato e testato")
    await b.goals.set_status(ko, "failed", "DNS non risolve")
    _insert_tool(b)
    await b.lessons.add("Controlla sempre che l'URL esista prima di scaricarlo", "fix")
    ev = await b.bus.publish("goal.update", None, **b.goals.get(ok))
    w._on_event(ev)
    w._on_event(await b.bus.publish("tool.created", "exec-1", name="somma_numeri", description="somma due numeri", passed=True))

    assert await w.sync_all() > 0
    d = w.dir
    ep = (d / f"episodes/goal-{ok}.md").read_text()
    assert "riuscito" in ep and "tool somma creato e testato" in ep and "[[status]]" in ep
    assert "fallito" in (d / f"episodes/goal-{ko}.md").read_text()
    assert "somma_numeri" in (d / "tools/somma_numeri.md").read_text()
    assert "URL esista" in (d / "lessons.md").read_text()
    status, index = (d / "status.md").read_text(), (d / "index.md").read_text()
    assert f"[[episodes/goal-{ok}]]" in status and "[[tools/somma_numeri]]" in status
    assert f"[[episodes/goal-{ko}]]" in index and "## Tool creati (1)" in index

    log = (d / "log.md").read_text()
    entries = re.findall(r"^## \[\d{4}-\d{2}-\d{2} \d{2}:\d{2}\] (\w+) \| (.+)$", log, re.M)
    assert ("goal", f"#{ok} Creare il tool somma") in entries and ("tool", "somma_numeri (test OK)") in entries
    # a second sync changes nothing and does not duplicate log lines
    w._on_event(ev)
    assert await w.sync_all() == 0
    assert (d / "log.md").read_text() == log


async def test_scan_picks_up_manual_edits_and_deletions(wb):
    w = wb.wiki
    (w.dir / "concepts").mkdir(exist_ok=True)
    (w.dir / "concepts/mia-idea.md").write_text("# La mia idea\n\nScritta a mano in Obsidian, collegata a [[status]].\n")
    assert w.scan() == 1
    row = wb.db.one("SELECT title,type,managed,links FROM wiki_pages WHERE id='concepts/mia-idea'")
    assert row["title"] == "La mia idea" and row["managed"] == "user" and json.loads(row["links"]) == ["status"]
    assert w.scan() == 0
    (w.dir / "concepts/mia-idea.md").unlink()
    assert w.scan() == 1 and wb.db.one("SELECT id FROM wiki_pages WHERE id='concepts/mia-idea'") is None


async def test_lint_finds_broken_links_orphans_contradictions(wb):
    w = wb.wiki
    w.write_page("concepts/uno", "Uno", "concept", "primo", "Rimanda a [[Pagina Inesistente]] e [[concepts/due]].", managed="llm")
    w.write_page("concepts/due", "Due", "concept", "secondo", "⚠ Contraddizione: prima diceva A, ora B.", managed="llm")
    w.write_page("concepts/solo", "Solo", "concept", "nessuno mi cita", "Pagina isolata.", managed="llm")
    rep = w.lint()
    assert rep["broken"] == [{"target": "Pagina Inesistente", "from": ["concepts/uno"]}]
    assert rep["orphans"] == ["concepts/solo", "concepts/uno"]
    assert rep["contradictions"] == ["concepts/due"] and rep["total"] == 4
    assert "Pagina Inesistente" in (w.dir / "lint.md").read_text()
    assert wb.db.kv_get("wiki_wanted")[0]["target"] == "Pagina Inesistente"


async def test_keyword_search_without_vectors_and_note_filing(wb):
    w = wb.wiki
    w.write_page("concepts/podman", "Sandbox Podman", "concept", "Container per eseguire i tool", "I tool girano in un container Podman rootless.", managed="llm")
    w.write_page("concepts/lm-studio", "LM Studio", "concept", "Server del modello locale", "Serve il modello Qwen via API compatibile OpenAI.", managed="llm")
    hits = await w.search("dove girano i tool nel container?")
    assert hits[0]["id"] == "concepts/podman"
    assert await w.search("zxqwv inesistente") == []
    pid = await w.note("Perche' Podman e non Docker", "Podman e' rootless e non richiede un demone: piu' sicuro per codice scritto dagli agenti.", source="agent:exec-1")
    assert pid == "notes/perche-podman-e-non-docker"
    assert (await w.search("demone rootless sicuro"))[0]["id"] == pid
    assert "## [" in (w.dir / "log.md").read_text() and "query | Archiviata" in (w.dir / "log.md").read_text()
    with pytest.raises(ValueError):
        await w.note("x", "corto")


async def test_agent_tools_wiki_search_read_note(wb):
    w = wb.wiki
    w.write_page("insights/cache", "Cache dei tool", "insight", "I tool pytest vanno cachati", "Rieseguire i test costa 20 secondi.", managed="llm")
    ctx = ToolContext(wb, Agent(wb, "executor", "prova"))
    hits = await wb.tools.call(ctx, "wiki_search", {"query": "test pytest cache"})
    assert hits[0]["id"] == "insights/cache"
    page = await wb.tools.call(ctx, "wiki_read", {"id": "insights/cache"})
    assert "20 secondi" in page["body"]
    assert "non trovata" in await wb.tools.call(ctx, "wiki_read", {"id": "insights/nope"})
    assert "archiviata come [[notes/" in await wb.tools.call(ctx, "wiki_note", {"title": "Scoperta sui test", "body": "Con -x i test si fermano al primo errore."})


async def test_vectors_semantic_search_and_2d_map_with_real_embedding_model(wb):
    w = wb.wiki
    pages = [
        ("concepts/cucina", "Cucina italiana", "La pasta si cuoce in acqua salata bollente con un filo d'olio."),
        ("concepts/astronomia", "Astronomia", "I pianeti orbitano attorno alle stelle e la luna gira attorno alla terra."),
        ("concepts/database", "Database", "Le tabelle relazionali usano indici e chiavi primarie per le query."),
        ("concepts/calcio", "Calcio", "La squadra segna un goal nello stadio davanti ai tifosi."),
    ]
    for pid, title, body in pages:
        w.write_page(pid, title, "concept", body[:60], body + " Vedi [[status]].", managed="llm")
    res = await w.embed_pending(force=True)
    assert res["available"] is True and res["embedded"] >= 4, res
    assert (await w.stats())["embedded"] == (await w.stats())["pages"]
    wb.settings.embeddings = "on"
    hits = await w.search("corpi celesti che girano intorno al sole")
    assert hits[0]["id"] == "concepts/astronomia", hits
    g = w.graph()
    assert len(g["nodes"]) >= 8 and all(n["embedded"] for n in g["nodes"])
    assert all(-1.0 <= n["sx"] <= 1.0 and -1.0 <= n["sy"] <= 1.0 for n in g["nodes"])
    assert len({(n["sx"], n["sy"]) for n in g["nodes"]}) == len(g["nodes"])  # distinct positions
    assert {"source": "concepts/cucina", "target": "status"} in g["edges"]


async def test_ingest_with_real_llm_creates_linked_pages_and_advances_cursor(wb):
    w = wb.wiki
    await wb.memory.add("fact", "Brain salva ricordi, giornale e obiettivi in un database SQLite locale chiamato brain.db", ["db"], 0.8)
    await wb.memory.add("insight", "Le richieste parallele a LM Studio vengono serializzate: piu' agenti non accelerano la generazione", ["llm"], 0.9)
    await wb.memory.add("user", "Lorenzo ha detto: la chat deve avere la priorita' sugli agenti", ["user"], 0.9)
    wb.memory.journal_add("outcome", "#2 Chat con priorita': FATTO - le richieste degli agenti vengono interrotte quando arriva un messaggio di Lorenzo")
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
    assert re.search(r"^## \[[\d\- :]+\] ingest \| \d+ pagine da 3 ricordi e 1 voci", log, re.M)
    assert await w.ingest() == {"pages": 0, "items": 0}  # nothing new


async def test_ingest_page_validation_sources_and_user_pages_are_protected(wb):
    w = wb.wiki
    pid = w._ingest_page({"id": "concepts/x y", "title": "Idea valida", "type": "insight", "summary": "s", "sources": ["memory:4", "<script>"],
                          "body": "Vedi [[memory:4]] e [[status]]: una pagina con abbastanza testo per passare."})
    assert pid == "insights/idea-valida"  # bad id falls back to type folder + slug
    page = w.read_page(pid)
    assert page["sources"] == ["memory:4"] and "[[memory:4]]" not in page["body"] and "(memory:4)" in page["body"]
    assert w._ingest_page({"title": "ab", "body": "corto"}) is None
    (w.dir / "concepts").mkdir(exist_ok=True)
    (w.dir / "concepts/mia.md").write_text("# Mia\n\nPagina di Lorenzo, da non toccare mai.\n")
    w.scan()
    assert w._ingest_page({"id": "concepts/mia", "title": "Mia", "body": "Il bibliotecario prova a riscriverla con testo abbastanza lungo."}) is None
    assert "da non toccare" in (w.dir / "concepts/mia.md").read_text()
    assert w._ingest_page({"id": "episodes/goal-1", "title": "Finto", "type": "concept", "body": "Prova a invadere una cartella gestita dal database."}) == "concepts/finto"


async def test_reset_wipes_the_wiki(wb):
    w = wb.wiki
    await w.note("Da dimenticare", "Questa nota deve sparire con il reset di fabbrica.")
    assert (w.dir / "notes/da-dimenticare.md").exists()
    wb.db.wipe()
    await w.reset()
    assert not list((w.dir / "notes").glob("*.md"))
    assert wb.db.one("SELECT id FROM wiki_pages WHERE id='notes/da-dimenticare'") is None
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
        assert page["title"] == "Stato del progetto" and page["links"]
        assert "# Indice" in c.get("/api/v1/wiki/page", params={"id": "index"}).json()["body"]
        assert c.get("/api/v1/wiki/page", params={"id": "nope/x"}).status_code == 404
        assert c.get("/api/v1/wiki/page", params={"id": "../../etc/passwd"}).status_code == 404
        hits = c.get("/api/v1/wiki/search", params={"q": "stato del progetto obiettivi"}).json()
        assert hits and hits[0]["id"] == "status"
