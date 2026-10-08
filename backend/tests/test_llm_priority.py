import asyncio

import pytest

from brain.llm import AGENT_PRIORITY, CHAT_PRIORITY, LLMError, _Call, _Gate


async def test_chat_jumps_ahead_of_queued_agent_requests():
    gate = _Gate(1)
    await gate.acquire(AGENT_PRIORITY)  # an agent is generating
    order: list[str] = []

    async def take(name: str, prio: int):
        await gate.acquire(prio)
        order.append(name)
        gate.release()

    a = asyncio.create_task(take("agent-a", AGENT_PRIORITY))
    b = asyncio.create_task(take("agent-b", AGENT_PRIORITY))
    await asyncio.sleep(0.05)
    gate.chat_pending += 1
    c = asyncio.create_task(take("chat", CHAT_PRIORITY))
    await asyncio.sleep(0.05)
    gate.chat_pending -= 1
    gate.release()
    await asyncio.wait_for(asyncio.gather(a, b, c), 5)
    assert order[0] == "chat"


async def test_agents_wait_while_a_chat_is_pending_even_with_a_free_slot():
    gate = _Gate(2)
    gate.chat_pending = 1
    agent = asyncio.create_task(gate.acquire(AGENT_PRIORITY))
    await asyncio.sleep(0.1)
    assert not agent.done() and gate.in_use == 0
    await asyncio.wait_for(gate.acquire(CHAT_PRIORITY), 1)  # the chat itself is never held back
    gate.chat_pending = 0
    gate.dispatch()
    await asyncio.wait_for(agent, 1)
    assert gate.in_use == 2


async def test_preempt_aborts_only_agent_requests(brain):
    agent_call = _Call(AGENT_PRIORITY, "exec-1", runner=asyncio.create_task(asyncio.sleep(60)))
    chat_call = _Call(CHAT_PRIORITY, "voice", runner=asyncio.create_task(asyncio.sleep(60)))
    brain.llm._active |= {agent_call, chat_call}
    brain.llm._preempt_lower()
    await asyncio.sleep(0.05)
    assert agent_call.preempted and agent_call.runner.cancelled()
    assert not chat_call.preempted and not chat_call.runner.done()
    chat_call.runner.cancel()


async def test_embeddings_are_skipped_unless_their_model_is_already_loaded(brain):
    """Asking LM Studio for an unloaded embedding model makes it evict the chat model."""
    llm = brain.llm
    states = await llm.model_states()
    loaded = states.get(brain.settings.embed_model) == "loaded"
    assert await llm.embeddings_available() == loaded
    if not loaded:
        with pytest.raises(LLMError, match="not loaded"):
            await llm.embed(["ciao"])
        await brain.memory.add("fact", "Roma e' la capitale d'Italia")  # falls back to keyword search
        assert (await brain.memory.search("capitale", 1))[0]["text"].startswith("Roma")


async def test_chat_preempts_a_running_agent_request_with_the_real_model(brain):
    """Real LM Studio: a long agent generation is aborted when chat arrives, the chat answers first, the agent restarts."""
    import time

    llm = brain.llm
    done: dict[str, float] = {}

    async def agent():
        await llm.chat([{"role": "user", "content": "Scrivi un saggio di 400 parole sulla storia della matematica."}], agent="exec-x", max_tokens=1200)
        done["agent"] = time.time()

    async def chat():
        await llm.chat([{"role": "user", "content": "Rispondi solo: ok"}], agent="voice", max_tokens=100, priority=0)
        done["chat"] = time.time()

    t_agent = asyncio.create_task(agent())
    for _ in range(120):  # wait until the agent request is really generating
        if llm.streams.get("exec-x"):
            break
        await asyncio.sleep(1)
    assert llm.streams.get("exec-x"), "agent request never started streaming"
    await chat()
    await asyncio.wait_for(t_agent, 400)
    assert llm.preemptions >= 1
    assert done["chat"] < done["agent"]
