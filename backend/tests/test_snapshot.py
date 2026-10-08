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


def test_rate_uses_a_sliding_window_and_ignores_time_before_the_first_token():
    from brain.llm import _Rate

    r = _Rate(window=3.0)
    assert r.value(100.0) == 0.0  # nothing generated yet, however long the queue wait
    for i in range(11):
        r.add(100.0 + i * 0.1)  # 10 tokens/s
    assert abs(r.value(101.1) - 10.0) < 0.5
    assert r.value(110.0) == 0.0  # idle for longer than the window


def test_aggregate_rate_sums_concurrent_streams():
    from brain.llm import _Rate

    total, a, b = _Rate(), _Rate(), _Rate()
    for i in range(21):
        t = 50.0 + i * 0.1
        a.add(t)
        b.add(t)
        total.add(t)
        total.add(t + 0.05)
    assert abs(a.value(52.0) - 10.0) < 0.5 and abs(b.value(52.0) - 10.0) < 0.5
    assert abs(total.value(52.0) - 20.0) < 1.5
