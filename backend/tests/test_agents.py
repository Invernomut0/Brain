import pytest

from brain.agents import Agent


async def test_agent_solves_task_with_real_tool_in_sandbox(brain):
    agent = Agent(
        brain, "executor",
        "Compute 17*23 using python_exec in the sandbox, then conclude with finish reporting the number in the summary.",
        max_steps=6,
    )
    res = await brain.orchestrator.run_agent(agent)
    assert res["success"], res
    assert "391" in res["summary"]
    assert any(t["tool"] == "python_exec" and t["ok"] for t in res["trace"])


async def test_one_full_cycle_plans_executes_critiques(brain):
    """Real end-to-end cycle: planner -> goal -> agent -> critic -> memory/journal."""
    brain.control.max_cycles = 0
    await brain.goals.add("Evolvere", "root", None, 1.0, None, status="active")
    await brain.orchestrator.plan()
    pending = brain.goals.pending()
    assert pending, "planner produced no goals"
    assert all(g["expected_success"] is not None for g in pending)
    brain.db.execute("UPDATE goals SET description=description || ' Limit the work to at most 3 steps.'")
    await brain.orchestrator.execute_goal(pending[0])
    g = brain.goals.get(pending[0]["id"])
    assert g["status"] in ("done", "failed", "pending")
    assert brain.memory.count() >= 1
    assert brain.selfmodel.metrics()["agents_spawned"] >= 1
