"""Holding advice survives one-recommendation-per-ticker cloud persistence."""
from copy import deepcopy

import pytest

from api.plan_service import plan_payload


def inputs():
    current = {"account": {"portfolio_version": 2}, "portfolio": {"cash": 90000},
               "positions": [{"ticker": "TEAM", "side": "LONG", "quantity": 46}]}
    quote = {"Ticker": "TEAM", "Price": 217.12, "Price As Of": "2026-10-15", "Score": 80}
    plan = {"strategy_name": "Test", "market_data_as_of": "2026-10-15",
            "buys": [{**quote, "Signal": "BUY", "Reason": "Addition qualifies",
                      "Allocation": 9987.52, "Quantity": 46}],
            "holdings": [{**quote, "Action": "KEEP", "Signal": "WAIT", "Reason": "No exit conditions"}]}
    return current, plan


def test_buy_addition_keeps_separate_holding_reason_without_duplicate_recommendation():
    current, plan = inputs()
    unchanged = deepcopy((current, plan))
    result = plan_payload(current, plan)
    assert (current, plan) == unchanged
    assert len(result["recommendations"]) == 1
    item = result["recommendations"][0]
    assert item["action"] == "BUY"
    assert item["reason"] == "Addition qualifies"
    assert item["score"] == 80
    assert item["factor_details"]["Proposed Quantity"] == 46
    assert item["factor_details"]["Holding Review"] == {
        "Action": "KEEP", "Signal": "WAIT", "Reason": "No exit conditions", "Score": 80,
    }


@pytest.mark.parametrize("action,signal,public_action", [("KEEP", "WAIT", "WAIT"), ("REVIEW EXIT", "AVOID", "SELL")])
def test_standalone_holding_review_preserves_existing_signal_mapping(action, signal, public_action):
    current, plan = inputs()
    plan["buys"] = []
    plan["holdings"][0].update(Action=action, Signal=signal)
    item = plan_payload(current, plan)["recommendations"][0]
    assert item["action"] == public_action
    assert item["factor_details"]["Holding Review"]["Action"] == action


def test_new_stock_does_not_get_an_invented_holding_review():
    current, plan = inputs()
    current["positions"] = []
    plan["holdings"] = []
    item = plan_payload(current, plan)["recommendations"][0]
    assert "Holding Review" not in item["factor_details"]
