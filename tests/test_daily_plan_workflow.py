"""Exercise the public planning route with real strategies and disposable inputs.

Authentication, price transport and cloud persistence are substituted; strategy,
regime, sizing and JSON conversion are the production implementations. No network
requests, portfolio files or real account records are touched.
"""
from copy import deepcopy
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from api.auth import Principal, require_principal
from api.public_app import create_app
from paper_trading.trading_plan import CONSENSUS_MODE


@pytest.mark.parametrize("cash,missing_holding", [(15000, False), (0, False), (15000, True)])
def test_daily_route_uses_real_consensus_and_keeps_account_input_unchanged(monkeypatch, cash, missing_holding):
    monkeypatch.setenv("STOCK_COMP_SUPABASE_URL", "https://storage.example.com")
    monkeypatch.setenv("STOCK_COMP_SUPABASE_PUBLISHABLE_KEY", "sb_publishable_test")
    monkeypatch.setenv("STOCK_COMP_SUPABASE_SECRET_KEY", "sb_secret_disposable_test")
    original = {
        "snapshot_time": datetime.now(timezone.utc).isoformat(), "data_mode": "cloud",
        "account": {"name": "Disposable", "starting_capital": 100000, "portfolio_version": 4},
        "portfolio": {"cash": cash},
        "positions": [{"ticker": "MISSING" if missing_holding else "AAPL", "side": "LONG",
                       "quantity": 10, "average_price": 100, "exact_cost_basis": None}],
        "plan": {"status": "NONE"}, "recommendations": [], "regime": {},
    }
    untouched = deepcopy(original)
    saved = []
    requests = []
    dates = pd.bdate_range(end=pd.Timestamp.now(tz="UTC").tz_localize(None).normalize() - pd.Timedelta(days=1), periods=300)

    def price_loader(tickers, start, end, **kwargs):
        requests.append(tuple(tickers))
        data = {}
        for ticker in tickers:
            if ticker == "MISSING":
                continue
            # Stocks outperform the two broad-market references, with sustained
            # momentum and mildly elevated recent volume. No fabricated signals.
            growth = 0.001 if ticker in {"SPY", "QQQ"} else 0.002
            closes = 75 * np.exp(np.arange(len(dates)) * growth)
            frame = pd.DataFrame({"Open": closes, "High": closes * 1.005,
                                  "Low": closes * 0.995, "Close": closes,
                                  "Volume": [1000000.] * (len(dates) - 5) + [1200000.] * 5}, index=dates)
            data[ticker] = frame.loc[pd.Timestamp(start):pd.Timestamp(end)]
        return data, {}

    async def load(_token):
        return original

    async def save(_token, payload, *, user_id):
        saved.append(payload)
        assert user_id == "11111111-1111-1111-1111-111111111111"
        result = deepcopy(original)
        result["plan"] = {"status": "CURRENT"}
        result["recommendations"] = payload["recommendations"]
        return result

    monkeypatch.setattr("data.market_data.load_market_data", price_loader)
    monkeypatch.setattr("api.plan_service.expanded_current_scanner_universe", lambda: ["MSFT", "NVDA", "MISSING"])
    monkeypatch.setattr("api.public_app.load_account_snapshot", load)
    monkeypatch.setattr("api.public_app.save_current_trading_plan", save)
    app = create_app()
    app.dependency_overrides[require_principal] = lambda: Principal(
        "11111111-1111-1111-1111-111111111111", access_token="disposable-token")
    response = TestClient(app).post("/v1/plans/current", json={
        "strategy": CONSENSUS_MODE, "target_position_value": 10000,
        "minimum_price": 5, "universe_mode": "EXPANDED",
    })
    assert response.status_code == 200, response.text
    assert original == untouched
    assert len(saved) == 1
    payload = saved[0]
    assert payload["expected_portfolio_version"] == 4
    assert payload["portfolio_snapshot"]["cash"] == cash
    assert {"SPY", "QQQ"}.issubset(set().union(*map(set, requests)))
    assert payload["market_as_of"].startswith(str(dates[-1].date()))
    assert any("MISSING" in warning for warning in payload["warnings"])
    buys = [item for item in payload["recommendations"] if item["action"] == "BUY"]
    if cash and not missing_holding:
        assert buys, "A clean multi-strategy synthetic uptrend should qualify"
        assert sum(item["factor_details"]["Proposed Allocation"] for item in buys) <= cash
        for item in buys:
            details = item["factor_details"]
            assert details["Proposed Quantity"] == int(details["Proposed Quantity"])
            assert 0 < details["Proposed Allocation"] <= 10000
            assert details["Proposed Allocation"] == pytest.approx(details["Proposed Quantity"] * item["last_price"])
            assert "strateg" in item["reason"].lower()
            assert item["price_as_of"][:10] == payload["market_as_of"][:10]
    else:
        assert not buys, "Zero cash or an unpriced holding must not force allocation"
    if missing_holding:
        assert any("Paper actions unavailable:" in warning for warning in payload["warnings"])
