import time

import pytest
from fastapi.testclient import TestClient

from brain.agents import ROLE_TOOLS, Agent
from brain.api import create_app
from brain.core import Brain
from brain.results import MAX_OUTPUT, ResultsError
from brain.tools import ToolContext


def _ctx(brain, role="executor", goal_id=None):
    return ToolContext(brain, Agent(brain, role, "t", goal_id))


async def test_every_tool_call_keeps_its_full_output(brain):
    agent = Agent(brain, "executor", "t", goal_id=3)
    allowed = brain.tools.all()
    big = "line of data\n" * 600  # 7.8k characters: the agent only sees 2.5k, the Results panel keeps it all
    assert "ERROR" not in await agent._act("write_file", {"path": "data/big.txt", "content": big}, allowed)
    obs = await agent._act("read_file", {"path": "data/big.txt"}, allowed)
    assert len(obs) < len(big)
    await agent._act("read_file", {"path": "data/missing.txt"}, allowed)

    runs = brain.results.tool_runs()
    assert [r["tool"] for r in runs] == ["read_file", "read_file", "write_file"]  # newest first
    assert [r["ok"] for r in runs] == [False, True, True] and runs[1]["goal_id"] == 3 and runs[0]["ms"] >= 0
    full = brain.results.tool_run(runs[1]["id"])
    assert big.strip() in full["output"] and "data/big.txt" in full["args"]
    assert brain.results.tool_runs(ok=False)[0]["tool"] == "read_file" and len(brain.results.tool_runs(tool="write_file")) == 1
    assert brain.results.tool_names()[0] == "read_file"
    ev = brain.bus.recent(30, ["tool.result"])
    assert ev[-1]["data"]["run"] == runs[0]["id"]


async def test_a_huge_output_is_capped(brain):
    rid = brain.results.record_tool_run("exec-1", None, "python_exec", {"code": "x"}, True, 5, "y" * (MAX_OUTPUT + 500))
    out = brain.results.tool_run(rid)["output"]
    assert out.startswith("y" * 100) and "500 more characters" in out and len(out) < MAX_OUTPUT + 100


async def test_publish_artifact_through_the_agent_tool(brain):
    ctx = _ctx(brain, goal_id=7)
    await brain.tools.call(ctx, "write_file", {"path": "site/index.html", "content": "<h1>Price tracker</h1>"})
    msg = await brain.tools.call(ctx, "publish_artifact", {"path": "/workspace/site/index.html", "title": "Landing page", "description": "first draft"})
    assert "Landing page" in msg and "html" in msg
    [art] = brain.results.artifacts()
    assert art["kind"] == "html" and art["path"] == "site/index.html" and art["goal_id"] == 7 and art["agent"] == ctx.agent.id and art["exists"]

    await brain.tools.call(ctx, "write_file", {"path": "site/index.html", "content": "<h1>Price tracker v2</h1>"})
    await brain.tools.call(ctx, "publish_artifact", {"path": "site/index.html", "title": "Landing page v2"})
    [again] = brain.results.artifacts()  # same file: updated, not duplicated
    assert again["id"] == art["id"] and again["title"] == "Landing page v2"
    assert brain.bus.recent(5, ["artifact.published"])[-1]["data"]["title"] == "Landing page v2"

    for bad in ("../../etc/passwd", "/etc/hosts", "site/nope.html", "site"):
        with pytest.raises(ResultsError):
            await brain.results.publish_artifact(bad)


async def test_progress_reports_are_validated_and_ordered(brain):
    ctx = _ctx(brain, goal_id=2)
    ms = [{"title": "Landing page live", "done": True}, {"title": "First sale", "done": False}, "Newsletter", {"title": " "}]
    assert "35.5%" in await brain.tools.call(ctx, "report_progress", {"percent": 35.5, "summary": "  Page is up,\n checkout next ", "milestones": ms})
    await brain.tools.call(ctx, "report_progress", {"percent": 50, "summary": "Checkout works"})
    for bad in ({"percent": 101, "summary": "x"}, {"percent": -1, "summary": "x"}, {"percent": "lots", "summary": "x"}, {"percent": 10, "summary": "  "}):
        with pytest.raises(ResultsError):
            await brain.tools.call(ctx, "report_progress", bad)

    p = brain.results.progress()
    assert [h["percent"] for h in p["history"]] == [35.5, 50.0] and p["latest"]["summary"] == "Checkout works"
    assert p["history"][0]["summary"] == "Page is up, checkout next" and p["history"][0]["source"] == "agent"
    assert p["milestones"] == [{"title": "Landing page live", "done": True}, {"title": "First sale", "done": False}, {"title": "Newsletter", "done": False}]
    assert brain.bus.recent(3, ["progress.update"])[-1]["data"]["percent"] == 50.0


async def test_files_written_while_working_are_registered_automatically(brain):
    ctx = _ctx(brain, goal_id=5)
    await brain.tools.call(ctx, "write_file", {"path": "old/before.md", "content": "# old"})
    time.sleep(0.05)
    since = time.time()
    for path, text in [("out/report.md", "# Report"), ("out/data.csv", "a,b\n1,2"), ("out/helper.py", "x = 1"),
                       (".cache/hidden.html", "<p>x</p>"), ("tools/dbl.py", "def run(): pass"), ("out/page.html", "<p>hi</p>")]:
        await brain.tools.call(ctx, "write_file", {"path": path, "content": text})

    got = await brain.results.collect(since, "exec-1", 5)
    assert sorted(a["path"] for a in got) == ["out/data.csv", "out/page.html", "out/report.md"]  # no code, hidden or tool files
    assert all(a["auto"] for a in got) and {a["kind"] for a in got} == {"csv", "html", "markdown"}
    assert await brain.results.collect(since, "exec-1", 5) == []  # nothing new the second time

    await brain.tools.call(ctx, "publish_artifact", {"path": "out/report.md", "title": "Weekly report"})
    time.sleep(0.05)
    await brain.tools.call(ctx, "write_file", {"path": "out/report.md", "content": "# Report v2"})
    [changed] = await brain.results.collect(since, "exec-1", 5)  # a registered file that changed is refreshed
    assert changed["path"] == "out/report.md" and changed["title"] == "Weekly report" and not changed["auto"]
    assert changed["size"] == len("# Report v2")


async def test_goal_end_adds_a_system_entry_that_carries_the_agent_percentage(brain):
    await brain._bootstrap()
    root = brain.goals.root()["id"]
    a = await brain.goals.add("Build the page", "", root)
    await brain.goals.add("Collect payments", "", root)
    await brain.goals.set_status(a, "done", "ok")

    first = await brain.results.goal_finished(brain.goals.get(a), True, "ok")
    assert first["source"] == "system" and first["percent"] == 50.0 and "1 done" in first["summary"] and "1 queued" in first["summary"]

    await brain.results.report_progress(40, "Page is live", None, "exec-1", a)
    again = await brain.results.goal_finished(brain.goals.get(a), True, "ok")
    assert again["percent"] == 40.0  # the agent's own figure wins over the estimate


def test_agents_get_the_results_tools():
    assert "publish_artifact" in ROLE_TOOLS["researcher"] and "report_progress" in ROLE_TOOLS["engineer"]
    assert "propose_prompt" not in ROLE_TOOLS["executor"] and "publish_artifact" in ROLE_TOOLS["executor"]  # executors never evolve prompts


def test_results_endpoints(settings):
    settings.autostart = False
    brain = Brain(settings)
    with TestClient(create_app(brain)) as c:
        assert c.get("/api/v1/progress").json()["latest"] is None
        assert c.get("/api/v1/artifacts").json() == {"items": []}
        assert c.get("/api/v1/tool-runs").json() == {"items": [], "tools": []}

        ws = settings.workspace_dir
        (ws / "site").mkdir(parents=True, exist_ok=True)
        (ws / "site" / "index.html").write_text("<script>document.title='x'</script><h1>Hi</h1>")
        (ws / "site" / "notes.md").write_text("# Notes\n- one")
        secret = settings.workspace_dir.parent / "secret.txt"
        secret.write_text("TOP SECRET")

        import asyncio

        loop = asyncio.new_event_loop()
        html = loop.run_until_complete(brain.results.publish_artifact("site/index.html", "Page", "demo", "exec-1", 1))
        md = loop.run_until_complete(brain.results.publish_artifact("site/notes.md", "Notes"))
        loop.run_until_complete(brain.results.report_progress(20, "Started", [{"title": "Page", "done": True}], "exec-1", 1))
        rid = brain.results.record_tool_run("exec-1", 1, "web_search", {"query": "x"}, True, 12, "result text")
        loop.close()

        items = c.get("/api/v1/artifacts").json()["items"]
        assert [i["title"] for i in items] == ["Notes", "Page"] or [i["title"] for i in items] == ["Page", "Notes"]
        r = c.get(f"/api/v1/artifacts/{html['id']}/raw")
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/html") and "Hi" in r.text
        assert "sandbox" in r.headers["content-security-policy"] and r.headers["x-content-type-options"] == "nosniff"
        m = c.get(f"/api/v1/artifacts/{md['id']}/raw")
        assert m.headers["content-type"].startswith("text/plain") and "# Notes" in m.text  # never rendered as a page
        assert "attachment" in c.get(f"/api/v1/artifacts/{md['id']}/raw?download=1").headers["content-disposition"]
        assert c.get("/api/v1/artifacts/999/raw").status_code == 404

        # a registered path cannot be turned into a way to read other files
        brain.db.execute("UPDATE artifacts SET path=? WHERE id=?", ("../secret.txt", md["id"]))
        assert "TOP SECRET" not in c.get(f"/api/v1/artifacts/{md['id']}/raw").text
        assert c.get(f"/api/v1/artifacts/{md['id']}/raw").status_code == 403

        p = c.get("/api/v1/progress").json()
        assert p["latest"]["percent"] == 20.0 and p["milestones"] == [{"title": "Page", "done": True}]
        runs = c.get("/api/v1/tool-runs?tool=web_search").json()
        assert runs["items"][0]["id"] == rid and runs["tools"] == ["web_search"]
        assert c.get(f"/api/v1/tool-runs/{rid}").json()["output"] == "result text"
        assert c.get("/api/v1/tool-runs/9999").status_code == 404

        assert c.delete(f"/api/v1/artifacts/{html['id']}").json() == {"deleted": html["id"]}
        assert c.delete(f"/api/v1/artifacts/{html['id']}").status_code == 404
        assert (ws / "site" / "index.html").exists()  # only the listing entry goes away
