import sys

sys.path.insert(0, "/evolvable/hooks")

from prioritize import prioritize  # noqa: E402


def test_prioritize_orders_by_priority():
    goals = [
        {"id": 1, "priority": 0.2, "attempts": 0},
        {"id": 2, "priority": 0.9, "attempts": 0},
    ]
    assert prioritize(goals, {}) == [2, 1]
