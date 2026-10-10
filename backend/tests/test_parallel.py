import asyncio
import time

from brain.agents import Agent


async def test_scheduler_runs_goals_in_parallel(brain):
    """Four goals, three slots: the first three must overlap and all four must finish."""
    brain.settings.max_parallel_agents = 3
    brain.settings.reflect_every = brain.settings.evolve_every = 10_000
    root = await brain.goals.add("root", "", None, 1.0, None, status="active")
    ids = [await brain.goals.add(f"g{i}", "", root, 0.5, 0.5) for i in range(4)]
    state = {"now": 0, "peak": 0}

    async def execute(goal):  # real asyncio work stands in for the LLM-bound agent run
        state["now"] += 1
        state["peak"] = max(state["peak"], state["now"])
        await asyncio.sleep(3)
        state["now"] -= 1
        await brain.goals.set_status(goal["id"], "done", "ok")

    async def no_plan():
        return None

    brain.orchestrator.execute_goal = execute
    brain.orchestrator.plan = no_plan
    started = time.time()
    await brain.start()
    for _ in range(60):
        if all(brain.goals.get(i)["status"] == "done" for i in ids):
            break
        await asyncio.sleep(1)
    elapsed = time.time() - started
    await brain.stop()
    await asyncio.wait_for(brain.orchestrator.task, 20)
    assert all(brain.goals.get(i)["status"] == "done" for i in ids)
    assert state["peak"] == 3
    assert brain.control.cycle == 4


async def test_stop_returns_running_goals_to_the_queue(brain):
    brain.settings.max_parallel_agents = 2
    root = await brain.goals.add("root", "", None, 1.0, None, status="active")
    ids = [await brain.goals.add(f"g{i}", "", root, 0.5, 0.5) for i in range(2)]

    async def execute(goal):
        await brain.goals.set_status(goal["id"], "active")
        await asyncio.sleep(60)

    async def no_plan():
        return None

    brain.orchestrator.execute_goal = execute
    brain.orchestrator.plan = no_plan
    await brain.start()
    for _ in range(30):
        if all(brain.goals.get(i)["status"] == "active" for i in ids):
            break
        await asyncio.sleep(1)
    await brain.kill()
    assert all(brain.goals.get(i)["status"] == "pending" for i in ids)


async def test_spawn_many_validates_input(brain):
    parent = Agent(brain, "executor", "t")
    assert (await brain.orchestrator.spawn_many(parent, [])).startswith("ERROR")
    assert (await brain.orchestrator.spawn_many(parent, ["x", {"role": "executor"}])).startswith("ERROR")


async def test_spawn_parallel_children_all_report_back_with_a_real_model(brain):
    """Sub-agents sharing one LLM slot must all run to completion and the parent must get every result."""
    brain.llm._gate.n = 1  # worst case: a single LM Studio slot for all of them
    parent = Agent(brain, "executor", "coordinate")
    task = 'Do nothing else: immediately reply with action "finish" and args {"success": true, "summary": "%s"}.'
    specs = [{"role": "executor", "task": task % tag} for tag in ("alpha", "beta", "gamma")]
    out = await asyncio.wait_for(brain.orchestrator.spawn_many(parent, specs), 600)
    lines = out.splitlines()
    assert len(lines) == 3 and all("success=True" in l for l in lines), out
    assert all(tag in out for tag in ("alpha", "beta", "gamma"))
    assert not brain.orchestrator.live  # every child was released


async def test_user_reply_reopens_failed_goal_and_reaches_waiting_agent(brain):
    root = await brain.goals.add("root", "", None, 1.0, None, status="active")
    gid = await brain.goals.add("Benchmark with Lorenzo", "ask for a threshold", root, 0.5, 0.5)
    await brain.goals.set_status(gid, "failed", "no reply")
    agent = Agent(brain, "executor", "t", goal_id=gid)

    # a question nobody is waiting for any more: the reply must reopen the goal
    asyncio.create_task(brain.orchestrator.ask(agent, "Which threshold?", 0))
    await asyncio.sleep(0.2)
    facts, reopened = await brain.orchestrator.route_user_message("threshold 0.99")
    assert reopened == gid and brain.goals.get(gid)["status"] == "pending"
    assert "threshold 0.99" in brain.goals.get(gid)["description"]
    assert any("reopened" in f for f in facts)

    # an agent blocked in ask_user receives the reply as the tool result
    waiter = asyncio.create_task(brain.orchestrator.ask(agent, "Another question?", 30))
    await asyncio.sleep(0.3)
    facts, _ = await brain.orchestrator.route_user_message("reply two")
    assert await asyncio.wait_for(waiter, 5) == "reply two"
    assert any("delivered" in f for f in facts)
