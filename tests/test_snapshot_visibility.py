from copy import deepcopy

from api.snapshot_visibility import visible_snapshot


def test_replaced_plan_hides_old_regime_and_recommendations_without_mutating_history():
    original = {
        "account": {"portfolio_version": 2},
        "plan": {"status": "REPLACED", "strategy": "Momentum V2"},
        "regime": {"name": "STRONG_UPTREND", "preferred_strategies": ["Aggressive Momentum V1"]},
        "recommendations": [{"ticker": "AAPL", "action": "BUY"}],
    }
    saved = deepcopy(original)

    shown = visible_snapshot(original)

    assert shown["plan"] == original["plan"]
    assert shown["regime"]["name"] == "UNAVAILABLE"
    assert shown["regime"]["preferred_strategies"] == []
    assert shown["recommendations"] == []
    assert original == saved


def test_current_plan_retains_signals_and_returns_detached_copy():
    original = {
        "plan": {"status": "CURRENT"},
        "regime": {"name": "SIDEWAYS"},
        "recommendations": [{"ticker": "MSFT"}],
    }
    shown = visible_snapshot(original)
    assert shown == original
    shown["recommendations"].clear()
    assert original["recommendations"] == [{"ticker": "MSFT"}]
