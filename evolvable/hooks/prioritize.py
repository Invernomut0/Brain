"""Hook: execution order of goals (evolvable by the system)."""


def prioritize(goals, state):
    """Return the goal ids in the desired execution order."""
    return [g["id"] for g in sorted(goals, key=lambda g: (-(g["priority"] or 0) + 0.2 * (g["attempts"] or 0), g["id"]))]
