"""Present saved-plan facts without mistaking them for current signals."""

from __future__ import annotations

from copy import deepcopy


def visible_snapshot(snapshot: dict) -> dict:
    """Hide plan-derived signals when the portfolio no longer matches the plan.

    The saved plan remains available for history; this only changes the
    response shown as the *current* account snapshot.
    """
    result = deepcopy(snapshot)
    plan = result.get("plan") or {}
    if plan.get("status") == "CURRENT":
        return result
    result["recommendations"] = []
    result["regime"] = {
        "name": "UNAVAILABLE",
        "label": "Unavailable",
        "reason": "The latest saved plan is not current for this portfolio. Generate a new plan to refresh its market regime.",
        "trend_strength": None,
        "market_breadth": None,
        "volatility": None,
        "preferred_strategies": [],
    }
    return result
