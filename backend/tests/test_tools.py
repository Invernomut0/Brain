import pytest

from brain.agents import Agent
from brain.tools import ToolContext

pytestmark = pytest.mark.asyncio

GOOD_CODE = '''
def run(text: str):
    words = [w for w in text.lower().split() if w]
    return {"words": len(words), "unique": len(set(words))}
'''
GOOD_TEST = '''
from word_stats import run

def test_counts():
    assert run("a b a") == {"words": 3, "unique": 2}

def test_empty():
    assert run("") == {"words": 0, "unique": 0}
'''
BAD_TEST = '''
from word_stats import run

def test_wrong():
    assert run("a b a")["words"] == 99
'''


def ctx(brain):
    return ToolContext(brain, Agent(brain, "engineer", "t"))


async def test_create_tool_registers_and_runs_in_sandbox(brain):
    r = await brain.tools.call(ctx(brain), "create_tool", dict(
        name="word_stats", description="conta parole", params={"text": "str"}, code=GOOD_CODE, test_code=GOOD_TEST))
    assert r["ok"], r
    assert "word_stats" in brain.tools.custom()
    out = await brain.tools.call(ctx(brain), "word_stats", {"text": "ciao ciao mondo"})
    assert out == {"words": 3, "unique": 2}


async def test_create_tool_with_failing_tests_is_rejected(brain):
    r = await brain.tools.call(ctx(brain), "create_tool", dict(
        name="word_stats", description="x", params={}, code=GOOD_CODE, test_code=BAD_TEST))
    assert not r["ok"]
    assert "word_stats" not in brain.tools.custom()


async def test_file_tools_block_path_traversal(brain):
    with pytest.raises(PermissionError):
        await brain.tools.call(ctx(brain), "read_file", {"path": "../../../etc/passwd"})


async def test_web_search_and_fetch_real_internet(brain):
    hits = await brain.tools.call(ctx(brain), "web_search", {"query": "Python programming language"})
    assert hits and hits[0]["url"].startswith("http")
    page = await brain.tools.call(ctx(brain), "web_fetch", {"url": "https://example.com"})
    assert page["status"] == 200 and "Example Domain" in page["text"]
