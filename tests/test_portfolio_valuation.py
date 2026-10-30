"""Read-only valuation never updates the imported account or its history."""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from api.auth import Principal, require_principal
from api.portfolio_valuation import value_portfolio, load_portfolio_valuation
from api.public_app import create_app

NOW = datetime(2026, 10, 30, 14, tzinfo=timezone.utc)


def account(side="LONG", **position):
    return {"account": {"portfolio_version": 2}, "portfolio": {"cash": 90000},
            "plan": {"id": "keep"}, "history": [{"action": "keep"}],
            "positions": [{"ticker": "TEAM", "side": side, "quantity": 10,
                           "average_price": 100, "exact_cost_basis": 1000, **position}]}


def prices(*values, dates=None):
    return {"TEAM": pd.DataFrame({"Close": values}, index=pd.to_datetime(dates or ["2026-10-29"]))}


def test_marks_do_not_change_input_or_history_and_prefer_exact_basis():
    source = account(average_price=999)
    before = deepcopy(source)
    result = value_portfolio(source, prices(120), now=NOW)
    assert result["portfolio"]["value"] == 91200
    assert result["portfolio"]["unrealized_pnl"] == pytest.approx(200)
    assert result["positions"][0]["price_as_of"].startswith("2026-10-29")
    assert source == before


def test_unfinished_and_future_closes_never_value_positions():
    result = value_portfolio(account(), prices(120, 900, 999,
        dates=["2026-10-29", "2026-10-30", "2026-11-02"]), now=NOW)
    assert result["positions"][0]["price"] == 120


@pytest.mark.parametrize("frame", [{}, prices(120, dates=["2026-10-15"]), prices(float("nan"))])
def test_missing_or_stale_price_never_uses_entry_price(frame):
    result = value_portfolio(account(), frame, now=NOW)
    assert result["portfolio"]["value"] is None
    assert result["portfolio"]["unrealized_pnl"] is None
    assert result["portfolio"]["cash"] == 90000
    assert result["warnings"]


def test_missing_basis_preserves_long_value_but_not_pnl():
    result = value_portfolio(account(average_price=None, exact_cost_basis=None), prices(120), now=NOW)
    assert result["portfolio"]["value"] == 91200
    assert result["portfolio"]["unrealized_pnl"] is None


def test_short_reuses_existing_collateral_accounting():
    result = value_portfolio(account("SHORT"), prices(80), now=NOW)
    assert result["portfolio"]["value"] == 91200
    assert result["portfolio"]["short_exposure"] == 800
    assert result["portfolio"]["unrealized_pnl"] == pytest.approx(200)


def test_empty_reset_account_needs_no_prices(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Empty portfolio must not download")
    monkeypatch.setattr("api.portfolio_valuation.load_market_data", forbidden)
    source = account(); source["positions"] = []; source["portfolio"]["cash"] = 100000
    result = asyncio.run(load_portfolio_valuation(source))
    assert result["portfolio"] == {"cash": 100000, "value": 100000,
        "long_exposure": 0, "short_exposure": 0, "unrealized_pnl": 0}


def test_provider_failure_is_safe_and_does_not_leak_error(monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("secret provider internals")
    monkeypatch.setattr("api.portfolio_valuation.load_market_data", fail)
    result = asyncio.run(load_portfolio_valuation(account()))
    assert result["portfolio"]["value"] is None
    assert "secret" not in str(result)


def test_route_loads_only_verified_callers_snapshot(monkeypatch):
    calls = []
    source = account(); source["positions"] = []
    async def read(token):
        calls.append(token); return source
    monkeypatch.setattr("api.public_app.load_account_snapshot", read)
    app = create_app()
    app.dependency_overrides[require_principal] = lambda: Principal("owner", access_token="caller-token")
    response = TestClient(app).get("/v1/portfolio-valuation")
    assert response.status_code == 200
    assert calls == ["caller-token"]
    assert response.headers["cache-control"] == "no-store"


def test_demo_cannot_start_market_load():
    app = create_app()
    app.dependency_overrides[require_principal] = lambda: Principal("demo", is_demo=True)
    assert TestClient(app).get("/v1/portfolio-valuation").status_code == 403
