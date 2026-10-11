from __future__ import annotations

from types import SimpleNamespace

from api.plan_service import account_inputs, build_cloud_plan, plan_payload
from data.scanner_universe import BROAD_CURRENT_SCANNER_UNIVERSE


def _snapshot():
    return {
        "account": {"starting_capital": 100000, "portfolio_version": 4},
        "portfolio": {"cash": 70000},
        "positions": [
            {"ticker": "AAPL", "side": "LONG", "quantity": 10, "average_price": 200, "exact_cost_basis": None},
            {"ticker": "MSFT", "side": "SHORT", "quantity": 5, "average_price": None, "exact_cost_basis": 2000},
        ],
    }


def test_account_snapshot_translates_without_inventing_trades():
    account, positions = account_inputs(_snapshot())

    assert account == {"Starting Capital": 100000.0, "Cash": 70000.0, "Realized P&L": 0.0}
    assert positions[0]["entry_price"] == 200
    assert positions[0]["allocated_capital"] == 2000
    assert positions[1]["entry_price"] == 400
    assert positions[1]["strategy"] == "Imported account snapshot"


def test_cloud_plan_passes_optional_minimum_and_marks_sub_five_research_only(monkeypatch):
    captured = {}

    def fake_engine(*args, **kwargs):
        captured.update(kwargs)
        return {"buys": [], "holdings": [], "market_data_as_of": "2026-09-30"}

    monkeypatch.setattr("api.plan_service.load_and_build_trading_plan", fake_engine)
    plan = build_cloud_plan(_snapshot(), minimum_price=12.50)
    assert captured["min_stock_price"] == 12.50
    assert plan["user_minimum_price"] == 12.50
    import pytest
    research = build_cloud_plan(_snapshot(), minimum_price=0)
    assert captured["min_stock_price"] == 0
    assert research["research_only_price_filter"] is True
    with pytest.raises(ValueError, match="Minimum stock price"):
        build_cloud_plan(_snapshot(), minimum_price=-0.01)


def test_broad_universe_is_opt_in_and_does_not_change_strategy_inputs(monkeypatch):
    captured = []

    def fake_engine(*args, **kwargs):
        captured.append(kwargs)
        return {"buys": [], "holdings": [], "market_data_as_of": "2026-10-08"}

    monkeypatch.setattr("api.plan_service.load_and_build_trading_plan", fake_engine)
    ordinary = build_cloud_plan(_snapshot())
    broad = build_cloud_plan(_snapshot(), universe_mode="BROAD")
    assert broad["universe_size"] == len(BROAD_CURRENT_SCANNER_UNIVERSE)
    assert broad["universe_size"] > ordinary["universe_size"]
    assert captured[0]["universe"] != captured[1]["universe"]
    for key in ("target_position_value", "min_stock_price", "whole_shares"):
        assert captured[0][key] == captured[1][key]


def test_live_universe_is_refreshed_for_each_plan(monkeypatch):
    captured = []
    calls = iter([["AAPL", "WBD"], ["AAPL"]])
    monkeypatch.setattr("api.plan_service.expanded_current_scanner_universe", lambda: next(calls))

    def fake_engine(*args, **kwargs):
        captured.append(kwargs["universe"])
        return {"buys": [], "holdings": [], "market_data_as_of": "2026-10-08"}

    monkeypatch.setattr("api.plan_service.load_and_build_trading_plan", fake_engine)
    build_cloud_plan(_snapshot())
    build_cloud_plan(_snapshot())
    assert captured == [["AAPL", "WBD"], ["AAPL"]]


def test_below_five_buy_is_explicitly_non_actionable_in_saved_payload():
    payload = plan_payload(_snapshot(), {
        "strategy_name": "Baseline", "user_minimum_price": 0,
        "research_only_price_filter": True, "market_data_as_of": "2026-10-08",
        "buys": [{"Ticker": "PENNY", "Score": 70, "Signal": "BUY", "Price": 4.99,
                  "Price As Of": "2026-10-08", "Reason": "Research signal."}],
        "holdings": [], "data_warnings": [],
    })
    assert payload["recommendations"][0]["factor_details"]["Research Only — Below $5"] is True
    assert payload["portfolio_snapshot"]["research_only_price_filter"] is True
    assert any("paper BUY" in warning for warning in payload["warnings"])


def test_plan_payload_maps_existing_engine_output_to_public_actions():
    features = SimpleNamespace(market_volatility_20d=0.012)
    regime = SimpleNamespace(
        regime="WEAK_UPTREND",
        confidence=0.75,
        reason="Mixed but constructive.",
        features=features,
        supporting_metrics={"trend": True},
    )
    plan = {
        "strategy_name": "Momentum V2",
        "price_session_end": "2026-09-30",
        "market_data_as_of": "2026-09-30T00:00:00+00:00",
        "data_warnings": ["One ticker unavailable."],
        "regime_result": regime,
        "buys": [{"Ticker": "NVDA", "Signal": "BUY", "Score": 88, "Reason": "Strong.", "Price": 100, "Price As Of": "2026-09-30", "Factor Details": {"Momentum": 0.2}, "Allocation": 20000, "Quantity": 200}],
        "holdings": [{"ticker": "AAPL", "Action": "REVIEW EXIT", "Signal": "AVOID", "Score": 20, "Reason": "Weak.", "Price": 190, "Price As Of": "2026-09-30"}],
    }

    payload = plan_payload(_snapshot(), plan)

    assert payload["expected_portfolio_version"] == 4
    assert [item["action"] for item in payload["recommendations"]] == ["BUY", "SELL"]
    assert payload["recommendations"][0]["factor_details"] == {"Momentum": 0.2, "Proposed Allocation": 20000, "Proposed Quantity": 200}
    assert payload["regime"]["name"] == "WEAK_UPTREND"
    assert payload["warnings"][0] == "One ticker unavailable."
    assert any("Paper actions unavailable:" in warning and "MSFT" in warning for warning in payload["warnings"])
    assert "1 buy" in payload["summary"]


def test_unscored_holding_blocks_paper_action_with_explicit_reason():
    snapshot = _snapshot()
    snapshot["positions"] = snapshot["positions"][:1]
    payload = plan_payload(snapshot, {
        "strategy_name": "Momentum V2", "market_data_as_of": "2026-10-08",
        "buys": [{"Ticker": "NVDA", "Score": 80, "Price": 100,
                  "Price As Of": "2026-10-08"}],
        "holdings": [{"ticker": "AAPL", "Action": "UNAVAILABLE", "Score": None,
                      "Price": 190, "Price As Of": "2026-10-08"}],
    })
    assert [row["ticker"] for row in payload["recommendations"]] == ["NVDA"]
    assert any("Paper actions unavailable:" in warning and "AAPL" in warning for warning in payload["warnings"])


def test_no_dated_prices_cannot_be_saved_as_a_fresh_plan():
    import pytest

    with pytest.raises(ValueError, match="No dated market price"):
        plan_payload(_snapshot(), {
            "strategy_name": "Baseline", "buys": [], "holdings": [],
            "data_warnings": [], "market_data_as_of": None,
        })
