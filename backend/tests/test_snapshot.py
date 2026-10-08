import asyncio


async def test_snapshot_reports_real_agent_states_after_a_reload(brain):
    bus = brain.bus
    await bus.publish("agent.spawn", "exec-1", role="executor", parent=None, goal_id=3, task="t")
    await bus.publish("agent.state", "exec-1", state="thinking", detail="")
    await bus.publish("agent.thought", "exec-1", thought="uso python", action="python_exec")
    await bus.publish("agent.state", "exec-1", state="acting", detail="python_exec")
    await bus.publish("agent.spawn", "critic-3", role="critic", parent=None, goal_id=3, task="valutazione")
    await bus.publish("agent.end", "critic-3", success=True, summary="ok", steps=1)
    agents = {a["id"]: a for a in brain.snapshot()["agents"]}
    assert agents["exec-1"]["state"] == "acting" and agents["exec-1"]["detail"] == "python_exec"
    assert agents["exec-1"]["thought"] == "uso python" and agents["exec-1"]["ended"] is None
    assert agents["critic-3"]["state"] == "done" and agents["critic-3"]["success"] is True


async def test_requests_waiting_for_lm_studio_are_reported_as_queued(brain):
    llm = brain.llm
    llm._sem = asyncio.Semaphore(1)
    sub = brain.bus.subscribe()
    async with llm._slot("a1"):
        waiter = asyncio.create_task(_enter(llm, "a2"))
        await asyncio.sleep(0.2)
        assert llm.queued == 1
    await asyncio.wait_for(waiter, 5)
    seen = []
    while not sub.empty():
        ev = sub.get_nowait()
        seen.append((ev.type, ev.agent, ev.data.get("state")))
    assert ("agent.state", "a2", "queued") in seen
    assert llm.queued == 0


async def _enter(llm, agent):
    async with llm._slot(agent):
        return True
