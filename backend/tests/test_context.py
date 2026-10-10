import json
import sqlite3

from fastapi.testclient import TestClient

from brain.agents import CORE_TOOLS, ROLE_TOOLS, Agent, tools_for
from brain.api import create_app
from brain.context import Step, WorkingMemory, mission, project_state
from brain.core import Brain
from brain.db import Database
from brain.evolution import validate_prompt
from brain.prompts import DEFAULTS

POLLUTED = (
    "You are a Brain Executor. Complete ONE goal using available tools.\n\n"
    "**TWO-STAGE CORRELATION DISCOVERY PROTOCOL (MANDATORY):**\nSave candidates to data/screen_candidates.json.\n"
    "**CHUNKED STATISTICAL TESTING PROTOCOL (MANDATORY):**\nLimit 3 tests.\n"
    'Reply ONLY with JSON: {"rationale": "...", "goals": []}\n\nHook prioritize.py:\ndef prioritize(goals, state):\n    return []\n' + "x" * 1800
)


def _step(n, action="write_file", ok=True, obs="done", args=None, note=""):
    return Step(n, json.dumps({"thought": f"t{n}", "action": action, "args": args or {}}), action, json.dumps(args or {}), ok, obs, note)


# ---------------------------------------------------------------- working memory
def test_working_memory_keeps_what_trimming_used_to_drop():
    mem = WorkingMemory()
    for i in range(1, 13):
        mem.add(_step(i, args={"path": f"out/f{i}.txt"}, obs="x" * 900, note="the key is in the vault" if i == 3 else ""))
    mem.add(_step(13, "web_search", ok=False, obs="ERROR timeout"))
    mem.pin("Marco", "I already have Stripe, I sent you the key")

    block = mem.block()
    assert "1. write_file" in block and "13. web_search" in block and "FAILED" in block  # every step stays, in one line
    assert "the key is in the vault" in block and "out/f12.txt" in block and "Still failing: web_search" in block
    assert "Marco: I already have Stripe" in block and len(block) < 3200

    pairs = mem.pairs(2000)
    assert len(pairs) < 2 * 13 and pairs[-1]["role"] == "user" and pairs[0]["role"] == "assistant"  # roles alternate, newest kept
    assert "OBSERVATION (web_search)" in pairs[-1]["content"]
    assert len(pairs[-2]["content"]) < 400 and "more characters" in pairs[-3]["content"]  # older observations are shortened

    mem.add(_step(14, "web_search", ok=True))
    assert "Still failing" not in mem.block()  # fixed errors stop being reported
    assert len(mem.pairs(1)) == 2  # the latest step always fits, whatever the budget


def test_mission_reminder_counts_down():
    assert "step 3/14" in mission("GOAL #1: build the page", 3, 14) and "LAST STEP" not in mission("g", 3, 14)
    assert "Wrap up" in mission("g", 13, 14) and "LAST STEP" in mission("g", 14, 14)
    assert len(mission("y" * 2000, 1, 5).splitlines()[0]) < 420


# ------------------------------------------------------------------ agent context
async def test_agent_context_is_lean_pinned_and_inspectable(brain):
    brain.settings.agent_context_chars = 800
    await brain.set_owner("Marco")
    await brain.lessons.add("Stripe webhooks need the raw request body to verify signatures", "fix")
    agent = Agent(brain, "executor", "GOAL #4: connect Stripe and test the checkout")
    agent._first = "TASK:\n" + agent.task
    agent._system_msg = agent._system()
    allowed = brain.tools.all(tools_for("executor"))
    for i in range(1, 11):
        obs = await agent._act("write_file", {"path": f"site/p{i}.html", "content": "<p>" + "z" * 800 + "</p>"}, allowed)
        agent.steps = i
        agent.mem.add(Step(i, json.dumps({"thought": "t", "action": "write_file", "args": {"path": f"site/p{i}.html"}}), "write_file", json.dumps({"path": f"site/p{i}.html"}), True, obs, f"page {i} saved" if i == 2 else ""))
    agent.steps = 11
    msgs = agent._build()

    system = msgs[0]["content"]
    assert "Self-model" not in system and "Identity:" not in system  # execution agents do not need the self-model
    assert "propose_prompt" not in system and "create_tool" in system and "You work for Marco." in system
    assert "Stripe webhooks" in system  # the lesson that matches the task
    assert msgs[1]["role"] == "user" and "WORKING MEMORY" in msgs[1]["content"] and "1. write_file" in msgs[1]["content"]
    assert "page 2 saved" in msgs[1]["content"] and "Files written: " in msgs[1]["content"]
    raw_pairs = len(msgs) - 2
    assert 2 <= raw_pairs < 20  # only the recent raw steps, the rest lives in the working memory
    assert msgs[-1]["role"] == "user" and msgs[-1]["content"].rstrip().endswith("left after this one.")
    assert "[MISSION] GOAL #4" in msgs[-1]["content"] and "step 11/14" in msgs[-1]["content"]

    ctx = brain.contexts[agent.id]
    assert [s["name"] for s in ctx["sections"]] == ["System prompt", "Task and project state", "Working memory", "Recent steps", "Mission reminder"]
    assert ctx["chars"] == sum(s["chars"] for s in ctx["sections"]) and ctx["tokens"] > 0 and ctx["step"] == 11 and ctx["budget_chars"] == 800
    assert any(m["section"] == "Working memory" for m in ctx["messages"])


async def test_context_endpoint(settings):
    settings.autostart = False
    brain = Brain(settings)
    with TestClient(create_app(brain)) as c:
        brain.set_context("exec-1", {"agent": "exec-1", "role": "executor", "step": 2, "tokens": 900, "sections": []})
        assert c.get("/api/v1/agents/exec-1/context").json()["tokens"] == 900
        assert c.get("/api/v1/agents/nobody/context").status_code == 404
        for i in range(50):
            brain.set_context(f"a-{i}", {"agent": f"a-{i}"})
        assert len(brain.contexts) == 40 and "exec-1" not in brain.contexts  # only recent agents are kept


def test_each_role_gets_its_own_tools():
    assert "propose_prompt" not in CORE_TOOLS and "propose_hook" not in CORE_TOOLS
    assert ROLE_TOOLS["evolver"] == ["propose_prompt", "propose_hook"]
    assert tools_for("analyst") == CORE_TOOLS  # custom roles spawned by agents
    assert "report_progress" in tools_for("researcher") and "create_tool" not in tools_for("researcher")


async def test_lessons_are_picked_by_relevance(brain):
    for text, kind in [("Stripe live keys must never be printed in summaries", "fix"), ("CSV files above 10k rows must be read in chunks", "fix"),
                       ("Always reply with ONE single JSON object {thought, action, args}", "format")]:
        await brain.lessons.add(text, kind)
    picked = brain.lessons.relevant("Create a Stripe checkout session for the product", 2)
    assert picked[0].startswith("Stripe live keys") and any("single JSON object" in p for p in picked)  # format rule always travels
    assert not any("CSV" in p for p in picked)
    fallback = brain.lessons.relevant("zzz qqq", 2)
    assert fallback and len(fallback) <= 2  # nothing matches: the most repeated ones


# ------------------------------------------------------------------ project state
async def test_goal_gets_a_digest_of_the_project(brain):
    await brain._bootstrap()
    await brain.set_owner("Marco")
    root = brain.goals.root()["id"]
    a = await brain.goals.add("Connect Stripe", "", root)
    await brain.goals.set_status(a, "done", "Stripe account acct_123 connected, live key stored in the vault")
    b = await brain.goals.add("Verify the Stripe balance", "", root, depends_on=[a])
    other = await brain.goals.add("Write the newsletter", "", root)
    await brain.goals.set_status(other, "done", "newsletter drafted")

    await brain.results.report_progress(40, "Payments are connected", [{"title": "Payments", "done": True}, {"title": "First sale", "done": False}], "exec-1", a)
    (brain.settings.workspace_dir / "site").mkdir(parents=True, exist_ok=True)
    (brain.settings.workspace_dir / "site" / "index.html").write_text("<h1>hi</h1>")
    await brain.results.publish_artifact("site/index.html", "Landing page")
    await brain.bus.publish("chat.message", None, role="user", text="I already have Stripe, I sent you the key")
    await brain.bus.publish("chat.message", None, role="brain", text="ok")

    state = await project_state(brain, brain.goals.get(b), "Verify the Stripe balance")
    assert "40%" in state and "Payments" in state and "First sale" in state
    assert "acct_123 connected" in state and "newsletter" not in state  # the dependency, not whatever finished last
    assert "Landing page (site/index.html)" in state and "Marco said recently" in state and "I already have Stripe" in state
    assert len(state) <= 2600

    # a goal without declared dependencies sees the latest completed goals instead
    plain = await project_state(brain, brain.goals.get(other), "x")
    assert "Latest goals completed" in plain and "Connect Stripe" in plain


async def test_goals_wait_for_their_dependencies(brain):
    await brain._bootstrap()
    root = brain.goals.root()["id"]
    a = await brain.goals.add("Build the page", "", root)
    b = await brain.goals.add("Collect payments", "", root, depends_on=[a])
    c = await brain.goals.add("Independent", "", root)
    assert brain.goals.deps(brain.goals.get(b)) == [a]
    assert not brain.goals.ready(brain.goals.get(b)) and brain.goals.ready(brain.goals.get(c))
    await brain.goals.set_status(a, "active")
    assert not brain.goals.ready(brain.goals.get(b))
    await brain.goals.set_status(a, "failed", "no luck")
    assert brain.goals.ready(brain.goals.get(b))  # a failed dependency does not block forever
    assert brain.goals.ready({"depends_on": "[9999]"}) and brain.goals.ready({})  # unknown or missing ids never block


def test_old_databases_get_the_new_columns(tmp_path):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE goals(id INTEGER PRIMARY KEY AUTOINCREMENT, parent_id INTEGER, title TEXT, description TEXT, status TEXT)")
    old.execute("CREATE TABLE prompt_versions(id INTEGER PRIMARY KEY AUTOINCREMENT, role TEXT, version INTEGER, sha TEXT, reason TEXT, created REAL, runs INTEGER DEFAULT 0, successes INTEGER DEFAULT 0, active INTEGER DEFAULT 1)")
    old.execute("INSERT INTO goals(title,status) VALUES('kept','done')")
    old.commit()
    old.close()
    db = Database(path)
    assert {r["name"] for r in db.query("PRAGMA table_info(goals)")} >= {"depends_on"}
    assert {r["name"] for r in db.query("PRAGMA table_info(prompt_versions)")} >= {"focus_sum", "focus_n"}
    assert db.one("SELECT title, depends_on FROM goals")["depends_on"] == "[]"


# ---------------------------------------------------------------- prompt hygiene
def test_prompt_rules_reject_what_polluted_the_executor():
    assert all(validate_prompt(role, text) is None for role, text in DEFAULTS.items())
    assert "length" in validate_prompt("executor", POLLUTED)
    body = POLLUTED[:900]
    assert "planner's output format" in validate_prompt("executor", body)
    no_contract = "You are a Brain Executor. Finish ONE goal with the tools you have; verify every result before you conclude. " * 2
    assert validate_prompt("executor", no_contract) is None
    assert "hook code" in validate_prompt("executor", no_contract + "\ndef prioritize(goals, state):\n    return []")
    assert "specific workspace files" in validate_prompt("executor", no_contract + " Save to data/screen_candidates.json first.")
    assert "MANDATORY" in validate_prompt("researcher", no_contract + " (MANDATORY) one. (MANDATORY) two.")
    assert "'verdict'" in validate_prompt("critic", "You judge goals strictly. Reply ONLY with JSON: {\"score\": 0-1}. " * 2)
    assert validate_prompt("planner", DEFAULTS["planner"]) is None and validate_prompt("nobody", no_contract)


async def test_a_polluted_prompt_is_put_back_at_startup(brain):
    p = brain.settings.evolvable_dir / "prompts" / "executor.md"
    p.write_text(POLLUTED)
    assert not (await brain.evolution.propose_prompt("executor", POLLUTED, "x"))["ok"]

    assert await brain.evolution.audit_prompts() == ["executor"]
    assert p.read_text() == DEFAULTS["executor"] and await brain.evolution.audit_prompts() == []
    row = brain.db.one("SELECT * FROM prompt_versions WHERE role='executor' AND active=1")
    assert row["version"] == 2 and row["reason"].startswith("audit:")
    assert brain.db.one("SELECT status FROM evolutions WHERE kind='prompt' ORDER BY id DESC")["status"] == "reset"

    p.write_text(POLLUTED)  # the same happens through the bootstrap path used at every start
    await brain._bootstrap()
    assert p.read_text() == DEFAULTS["executor"]


# ---------------------------------------------------------------------- focus
async def test_a_prompt_that_loses_focus_is_rolled_back(brain):
    old = brain.evolution.prompt("executor")
    new = "You are a hasty executor who acts first and reads later. " * 3
    assert (await brain.evolution.propose_prompt("executor", new, "test"))["ok"]
    for _ in range(4):
        brain.evolution.record_run("executor", True, 0.9)  # v1 era
    brain.db.execute("UPDATE prompt_versions SET runs=5, successes=5, focus_sum=4.5, focus_n=5 WHERE role='executor' AND version=1")
    brain.db.execute("UPDATE prompt_versions SET runs=6, successes=6, focus_sum=2.4, focus_n=6 WHERE role='executor' AND version=2")  # same success, drifting
    assert await brain.evolution.check_rollbacks() == ["executor"]
    assert brain.evolution.prompt("executor") == old
    assert brain.db.one("SELECT reason FROM evolutions WHERE status='rolled_back'")["reason"] == "focus regression"


async def test_focus_is_part_of_the_metrics(brain):
    assert brain.selfmodel.metrics()["focus"] is None
    brain.selfmodel.add_focus(0.8)
    brain.selfmodel.add_focus(0.4)
    brain.selfmodel.add_focus(7)  # clamped
    assert brain.selfmodel.metrics()["focus"] == round((0.8 + 0.4 + 1.0) / 3, 3)


async def test_run_stats_show_wandering(brain):
    agent = Agent(brain, "executor", "t")
    allowed = brain.tools.all(tools_for("executor"))
    for _ in range(3):
        await agent._act("list_files", {"path": "."}, allowed)  # the same call again and again
    await agent._act("read_file", {"path": "missing.txt"}, allowed)
    await agent._act("no_such_tool", {}, allowed)
    agent.steps = 5
    assert agent.stats() == {"steps": 5, "calls": 5, "repeats": 2, "errors": 2}


# --------------------------------------------------- with the real model (LM Studio)
async def test_real_agent_run_uses_the_working_memory_and_reports_stats(brain):
    brain.settings.agent_max_steps = 5
    agent = Agent(brain, "executor", "GOAL #1: save the text 'hello focus' in the file notes/hello.txt with write_file, then finish with success=true.")
    res = await brain.orchestrator.run_agent(agent)
    assert (brain.settings.workspace_dir / "notes" / "hello.txt").exists()
    assert res["steps"] <= 5 and res["stats"]["calls"] >= 1 and agent.mem.steps and any(f.endswith("notes/hello.txt") for f in agent.mem.files)
    ctx = brain.contexts[agent.id]
    assert ctx["step"] >= 1 and ctx["tokens"] > 0 and ctx["tokens"] < 12000


async def test_real_critic_returns_a_focus_score(brain):
    await brain._bootstrap()
    gid = await brain.goals.add("Write hello", "write hello into notes/hello.txt", brain.goals.root()["id"])
    res = {"success": True, "summary": "wrote the file", "trace": [{"tool": "write_file", "ok": True, "obs": "ok"}],
           "stats": {"steps": 2, "calls": 1, "repeats": 0, "errors": 0}}
    v = await brain.orchestrator.critique(brain.goals.get(gid), res)
    assert v["verdict"] in ("pass", "fail") and 0.0 <= v["focus"] <= 1.0
    wandering = dict(res, stats={"steps": 12, "calls": 12, "repeats": 10, "errors": 6})
    assert (await brain.orchestrator.critique(brain.goals.get(gid), wandering))["focus"] <= 0.6
