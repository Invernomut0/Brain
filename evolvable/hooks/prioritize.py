"""Hook: ordine di esecuzione degli obiettivi (evolvibile dal sistema)."""


def prioritize(goals, state):
    """Ritorna gli id degli obiettivi in ordine di esecuzione desiderato."""
    return [g["id"] for g in sorted(goals, key=lambda g: (-(g["priority"] or 0) + 0.2 * (g["attempts"] or 0), g["id"]))]
