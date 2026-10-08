import pytest

from brain.agents import Agent
from brain.tools import ToolContext


async def test_workspace_absolute_paths_are_normalised(brain):
    ctx = ToolContext(brain, Agent(brain, "executor", "t"))
    await brain.tools.call(ctx, "write_file", {"path": "/workspace/notes/a.txt", "content": "ciao"})
    assert (brain.settings.workspace_dir / "notes" / "a.txt").read_text() == "ciao"
    assert await brain.tools.call(ctx, "read_file", {"path": "workspace/notes/a.txt"}) == "ciao"
    assert "notes/a.txt" in await brain.tools.call(ctx, "list_files", {"path": "/workspace"})


async def test_real_traversal_is_still_blocked_with_a_helpful_message(brain):
    ctx = ToolContext(brain, Agent(brain, "executor", "t"))
    with pytest.raises(PermissionError, match="RELATIVI"):
        await brain.tools.call(ctx, "read_file", {"path": "../../etc/passwd"})


async def test_wrong_arguments_report_the_correct_signature(brain):
    ctx = ToolContext(brain, Agent(brain, "executor", "t"))
    with pytest.raises(TypeError, match="Firma corretta"):
        await brain.tools.call(ctx, "write_file", {"file": "x"})


async def test_agent_learns_a_lesson_from_error_then_fix(brain):
    agent = Agent(brain, "executor", "t")
    allowed = brain.tools.all()
    bad = await agent._act("write_file", {"path": "../../x.txt", "content": "a"}, allowed)
    assert bad.startswith("ERRORE PermissionError")
    good = await agent._act("write_file", {"path": "x.txt", "content": "a"}, allowed)
    assert not good.startswith("ERRORE")
    lessons = brain.lessons.all()
    assert len(lessons) == 1 and "write_file" in lessons[0]["text"] and lessons[0]["kind"] == "fix"
    assert brain.lessons.recent_failures()[0]["resolved"] is True
    # the lesson is injected in the next agent's system prompt
    assert "LEZIONI APPRESE" in Agent(brain, "executor", "t")._system()


async def test_unknown_tool_suggests_close_matches(brain):
    agent = Agent(brain, "executor", "t")
    obs = await agent._act("writefile", {}, brain.tools.all())
    assert "write_file" in obs


async def test_failed_create_tool_counts_as_failure_and_duplicates_bump_counter(brain):
    agent = Agent(brain, "engineer", "t")
    await agent._act("create_tool", {"name": "x", "description": "d", "params": {}, "code": "pass", "test_code": "pass"}, brain.tools.all())
    assert brain.lessons.recent_failures()[-1]["tool"] == "create_tool"
    assert await brain.lessons.add("Usa percorsi relativi al workspace per write_file", "fix") is True
    assert await brain.lessons.add("usa percorsi relativi al workspace per write_file!", "fix") is False
    assert [l["count"] for l in brain.lessons.all() if "relativi" in l["text"]] == [2]
