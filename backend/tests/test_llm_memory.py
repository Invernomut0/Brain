import pytest

pytestmark = pytest.mark.asyncio


async def test_model_resolution_and_json(brain):
    model = await brain.llm.resolve_model()
    assert "embed" not in model
    out = await brain.llm.chat_json(
        [{"role": "user", "content": 'Reply ONLY with JSON: {"sum": <result of 2+2>}'}], max_tokens=200, temperature=0
    )
    assert out["sum"] in (4, "4")
    assert brain.llm.total_tokens > 0


async def test_embeddings_and_semantic_memory(brain):
    await brain.memory.add("fact", "Rome is the capital of Italy", ["geo"])
    await brain.memory.add("fact", "The cheetah is the fastest land animal", ["zoo"])
    hits = await brain.memory.search("what is the capital of Italy?", 1)
    assert "Rome" in hits[0]["text"]
