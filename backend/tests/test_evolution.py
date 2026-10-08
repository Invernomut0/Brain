import pytest

pytestmark = pytest.mark.asyncio

NEW_HOOK = '''
def prioritize(goals, state):
    return [g["id"] for g in sorted(goals, key=lambda g: g["id"])]
'''
NEW_TEST = '''
import sys
sys.path.insert(0, "/evolvable/hooks")
from prioritize import prioritize

def test_fifo():
    assert prioritize([{"id": 5}, {"id": 2}], {}) == [2, 5]
'''
BROKEN_TEST = NEW_TEST.replace("[2, 5]", "[5, 2]")


async def test_seed_creates_versioned_prompts(brain):
    assert "Planner" in brain.evolution.prompt("planner")
    assert brain.db.one("SELECT COUNT(*) c FROM prompt_versions")["c"] >= 6


async def test_hook_runs_in_sandbox_and_evolves(brain):
    goals = [{"id": 1, "priority": 0.1, "attempts": 0}, {"id": 2, "priority": 0.9, "attempts": 0}]
    assert await brain.evolution.call_hook("prioritize", "prioritize", goals, {}) == [2, 1]
    r = await brain.evolution.propose_hook("prioritize", NEW_HOOK, NEW_TEST, "ordine fifo")
    assert r["ok"], r
    assert await brain.evolution.call_hook("prioritize", "prioritize", goals, {}) == [1, 2]


async def test_failing_hook_tests_are_rolled_back(brain):
    before = (brain.settings.evolvable_dir / "hooks" / "prioritize.py").read_text()
    r = await brain.evolution.propose_hook("prioritize", NEW_HOOK, BROKEN_TEST, "rotto")
    assert not r["ok"]
    assert (brain.settings.evolvable_dir / "hooks" / "prioritize.py").read_text() == before


async def test_prompt_regression_triggers_rollback(brain):
    old = brain.evolution.prompt("executor")
    new = "Sei un executor molto sbrigativo. " * 5
    assert (await brain.evolution.propose_prompt("executor", new, "test"))["ok"]
    assert brain.evolution.prompt("executor") == new
    # v1 performed well, v2 performs badly
    brain.db.execute("UPDATE prompt_versions SET runs=5, successes=5 WHERE role='executor' AND version=1")
    brain.db.execute("UPDATE prompt_versions SET runs=6, successes=1 WHERE role='executor' AND version=2")
    assert await brain.evolution.check_rollbacks() == ["executor"]
    assert brain.evolution.prompt("executor") == old


async def test_invalid_prompt_rejected(brain):
    assert not (await brain.evolution.propose_prompt("planner", "troppo corto", "x"))["ok"]
    assert not (await brain.evolution.propose_prompt("critic", "x" * 200, "senza contratto"))["ok"]
