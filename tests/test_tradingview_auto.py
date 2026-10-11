"""Automatic TradingView-style planning is advisory, dated, and bounded."""

from __future__ import annotations

from copy import deepcopy
from datetime import date

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from api.auth import Principal, require_principal
from api.demo_data import demo_snapshot
from api.public_app import create_app
from api.tradingview_auto import AutoPlanUnavailable, calculate_auto_long_plan, load_auto_long_plan


AS_OF = date(2026, 10, 8)


def prices() -> pd.DataFrame:
    dates = pd.bdate_range(end="2026-10-08", periods=30)
    close = [100 + i * 0.25 for i in range(30)]
    return pd.DataFrame({"High": [x + 2 for x in close],
                         "Low": [x - 2 for x in close], "Close": close}, index=dates)


def test_auto_size_respects_cash_risk_and_concentration():
    result = calculate_auto_long_plan("TEST", prices(), available_cash=5_000,
                                      account_equity=100_000, as_of=AS_OF)
    assert result["price_as_of"] == "2026-10-08"
    assert result["stop_watch_price"] < result["entry_price"] < result["take_profit_watch_price"]
    assert result["position_value"] <= 5_000
    assert result["position_value"] <= 20_000
    assert result["planned_risk_at_stop"] <= result["risk_budget"]
    assert result["stop_watch_price"] * 100 == pytest.approx(round(result["stop_watch_price"] * 100))
    assert result["take_profit_watch_price"] * 100 == pytest.approx(round(result["take_profit_watch_price"] * 100))
    assert result["cash_remaining"] >= 0
    assert calculate_auto_long_plan("TEST", prices(), available_cash=0,
                                    account_equity=100_000, as_of=AS_OF)["shares"] == 0


def test_auto_features_do_not_see_future_rows():
    before = calculate_auto_long_plan("TEST", prices(), available_cash=8_000,
                                      account_equity=100_000, as_of=AS_OF)
    changed = prices()
    changed.loc[pd.Timestamp("2026-10-09")] = {"High": 1000, "Low": 1, "Close": 800}
    after = calculate_auto_long_plan("TEST", changed, available_cash=8_000,
                                     account_equity=100_000, as_of=AS_OF)
    assert before == after


def test_auto_rejects_missing_stale_or_invalid_prices():
    with pytest.raises(AutoPlanUnavailable, match="21 dated"):
        calculate_auto_long_plan("TEST", prices().tail(10), available_cash=5_000,
                                 account_equity=100_000, as_of=AS_OF)
    with pytest.raises(AutoPlanUnavailable, match="stale"):
        calculate_auto_long_plan("TEST", prices(), available_cash=5_000,
                                 account_equity=100_000, as_of=date(2026, 10, 20))
    missing = prices()
    missing.loc[missing.index[-1], "Close"] = float("nan")
    with pytest.raises(AutoPlanUnavailable, match="missing"):
        calculate_auto_long_plan("TEST", missing, available_cash=5_000,
                                 account_equity=100_000, as_of=AS_OF)


def test_loader_requests_only_one_ticker_and_no_future_history():
    captured = []

    def loader(tickers, start, end):
        captured.append((tickers, start, end))
        return {"TEST": prices()}, {}

    result = load_auto_long_plan("TEST", available_cash=5_000, account_equity=100_000,
                                 as_of=AS_OF, load_market_data_fn=loader)
    assert result["ticker"] == "TEST"
    assert captured[0][0] == ["TEST"]
    assert captured[0][2] == AS_OF


def test_api_requires_current_buy_and_is_read_only(monkeypatch):
    import api.public_app as app_module

    snapshot = deepcopy(demo_snapshot())
    snapshot["plan"].update(status="CURRENT", strategy="All strategies — consensus",
                             market_as_of="2026-10-08T00:00:00Z")
    snapshot["recommendations"] = [{"ticker": "TEST", "action": "BUY", "status": "OPEN", "price": 107.25}]

    async def load(_token):
        return snapshot

    def auto(ticker, **kwargs):
        return calculate_auto_long_plan(ticker, prices(), as_of=AS_OF,
                                        available_cash=kwargs["available_cash"],
                                        account_equity=kwargs["account_equity"])

    def no_write(*_args, **_kwargs):
        raise AssertionError("Sizing must not mutate an account or place orders")

    monkeypatch.setattr(app_module, "load_account_snapshot", load)
    monkeypatch.setattr(app_module, "load_auto_long_plan", auto)
    monkeypatch.setattr(app_module, "save_current_trading_plan", no_write)
    monkeypatch.setattr(app_module, "update_competition_account_snapshot", no_write)
    application = create_app()
    application.dependency_overrides[require_principal] = lambda: Principal(
        user_id="user", access_token="fake-verified-token"
    )
    client = TestClient(application)
    payload = {"ticker": "TEST", "available_cash": 5_000, "account_equity": 100_000}
    assert client.post("/v1/planning-profiles/auto-plan", json=payload).status_code == 200
    assert client.post("/v1/plans/exit-guidance", json=payload).status_code == 200
    assert client.post("/v1/planning-profiles/auto-plan", json={**payload, "ticker": "OTHER"}).status_code == 422
    snapshot["recommendations"][0]["price"] = 80
    assert client.post("/v1/planning-profiles/auto-plan", json=payload).status_code == 409
    snapshot["recommendations"][0]["price"] = 107.25
    snapshot["plan"]["status"] = "STALE"
    assert client.post("/v1/planning-profiles/auto-plan", json=payload).status_code == 409
    snapshot["plan"]["status"] = "CURRENT"
    snapshot["plan"]["strategy"] = "Momentum V2"
    assert client.post("/v1/planning-profiles/auto-plan", json=payload).status_code == 200


def test_demo_cannot_request_automatic_market_data(monkeypatch):
    monkeypatch.setenv("STOCK_COMP_API_DEMO_MODE", "true")
    response = TestClient(create_app()).post("/v1/planning-profiles/auto-plan", json={
        "ticker": "AAPL", "available_cash": 5000, "account_equity": 100000,
    })
    assert response.status_code == 403
