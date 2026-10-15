from copy import deepcopy
from threading import Event
from time import sleep
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from api.auth import Principal, require_principal
from api.public_app import create_app

OWNER = "11111111-1111-1111-1111-111111111111"


def fixture_app(monkeypatch, build):
    for key, value in {"STOCK_COMP_SUPABASE_URL": "https://storage.example.com",
                       "STOCK_COMP_SUPABASE_PUBLISHABLE_KEY": "sb_publishable_test",
                       "STOCK_COMP_SUPABASE_SECRET_KEY": "sb_secret_test"}.items():
        monkeypatch.setenv(key, value)
    current = {"account": {"portfolio_version": 1}, "portfolio": {"cash": 100000},
               "positions": [], "recommendations": [], "plan": {"status": "NONE"}}
    saved = []

    async def load(_token):
        return deepcopy(current)

    async def save(_token, payload, *, user_id):
        saved.append((user_id, payload))
        current["plan"] = {"status": "CURRENT", "id": str(uuid4())}
        return deepcopy(current)

    monkeypatch.setattr("api.public_app.load_account_snapshot", load)
    monkeypatch.setattr("api.public_app.save_current_trading_plan", save)
    monkeypatch.setattr("api.public_app.build_cloud_plan", build)
    app = create_app()
    app.dependency_overrides[require_principal] = lambda: Principal(OWNER, access_token="test-session")
    return app, saved


def body():
    return {"request_id": str(uuid4()), "strategy": "Momentum V2", "universe_mode": "BROAD"}


def plan():
    return {"strategy_name": "Momentum V2", "market_data_as_of": "2026-10-14T00:00:00Z",
            "buys": [], "holdings": [], "no_purchase_explanation": "No qualified candidates."}


def terminal(client, job_id):
    for _ in range(100):
        result = client.get(f"/v1/plan-jobs/{job_id}")
        assert result.status_code == 200
        if result.json()["status"] != "RUNNING":
            return result.json()
        sleep(.005)
    pytest.fail("Test job did not finish")


def test_job_returns_immediately_reports_progress_deduplicates_and_saves_once(monkeypatch):
    entered, release = Event(), Event()

    def build(*args, status_callback):
        status_callback("DOWNLOADING", "AAPL (1/200)")
        entered.set()
        release.wait(2)
        return plan()

    app, saved = fixture_app(monkeypatch, build)
    with TestClient(app) as client:
        try:
            request = body()
            first = client.post("/v1/plan-jobs", json=request)
            assert first.status_code == 202
            job_id = first.json()["id"]
            assert entered.wait(.5)
            running = client.get(f"/v1/plan-jobs/{job_id}").json()
            assert running["stage"] == "DOWNLOADING"
            assert running["detail"] == "AAPL (1/200)"
            assert "owner" not in running and "token" not in str(running)
            assert client.post("/v1/plan-jobs", json=request).json()["id"] == job_id
            assert client.post("/v1/plan-jobs", json=body()).json()["id"] == job_id
            assert client.post("/v1/plan-jobs", json={**request, "minimum_price": 10}).status_code == 409
            app.dependency_overrides[require_principal] = lambda: Principal(str(uuid4()), access_token="other")
            assert client.get(f"/v1/plan-jobs/{job_id}").status_code == 404
            assert client.post("/v1/plan-jobs", json=body()).status_code == 429
            app.dependency_overrides[require_principal] = lambda: Principal(OWNER, access_token="test-session")
        finally:
            release.set()
        completed = terminal(client, job_id)
        assert completed["status"] == "SUCCEEDED"
        assert completed["snapshot"]["plan"]["status"] == "CURRENT"
        assert len(saved) == 1 and saved[0][0] == OWNER
        assert client.post("/v1/plan-jobs", json=request).json()["id"] == job_id
        assert len(saved) == 1


def test_timed_out_job_keeps_capacity_until_thread_exits_and_never_saves_late(monkeypatch):
    entered, release = Event(), Event()

    def build(*args, **kwargs):
        entered.set()
        release.wait(2)
        return plan()

    app, saved = fixture_app(monkeypatch, build)
    app.state.plan_jobs.timeout = .02
    with TestClient(app) as client:
        try:
            job_id = client.post("/v1/plan-jobs", json=body()).json()["id"]
            assert entered.wait(.5)
            failed = terminal(client, job_id)
            assert failed["status"] == "FAILED"
            assert "No new plan was saved" in failed["error"]
            assert client.post("/v1/plan-jobs", json=body()).status_code == 429
            assert not saved
        finally:
            release.set()
        for _ in range(100):
            if app.state.plan_jobs.active is None:
                break
            sleep(.005)
        assert app.state.plan_jobs.active is None
        assert not saved


@pytest.mark.parametrize("error,expected", [
    (ValueError("SPY/QQQ history unavailable"), "SPY/QQQ history unavailable"),
    (RuntimeError("private-token-and-path"), "No result was confirmed"),
    (HTTPException(409, "Portfolio changed while the plan was being generated."), "Portfolio changed"),
])
def test_job_failure_is_not_success_and_does_not_expose_unknown_exception(monkeypatch, error, expected):
    def build(*args, **kwargs):
        raise error
    app, saved = fixture_app(monkeypatch, build)
    with TestClient(app) as client:
        job_id = client.post("/v1/plan-jobs", json=body()).json()["id"]
        result = terminal(client, job_id)
        assert result["status"] == "FAILED"
        assert expected in result["error"]
        assert "private-token-and-path" not in str(result)
        assert not saved


def test_jobs_require_authentication_and_trusted_storage_before_build(monkeypatch):
    def build(*args, **kwargs):
        pytest.fail("Should not build")
    app, saved = fixture_app(monkeypatch, build)
    with TestClient(app) as client:
        monkeypatch.delenv("STOCK_COMP_SUPABASE_SECRET_KEY")
        monkeypatch.delenv("STOCK_COMP_SUPABASE_SERVICE_ROLE_KEY", raising=False)
        assert client.post("/v1/plan-jobs", json=body()).status_code == 503
        app.dependency_overrides[require_principal] = lambda: Principal("demo", is_demo=True)
        assert client.post("/v1/plan-jobs", json=body()).status_code == 403
        assert client.get(f"/v1/plan-jobs/{uuid4()}").status_code == 403
        app.dependency_overrides.clear()
        assert client.post("/v1/plan-jobs", json=body()).status_code == 401
        assert not saved


@pytest.mark.parametrize("endpoint", ["/v1/plan-jobs", "/v1/plans/current"])
def test_invalid_storage_credential_rejected_before_account_load_or_scan(monkeypatch, endpoint):
    def forbidden(*args, **kwargs):
        pytest.fail("Invalid storage configuration must fail before I/O")
    app, saved = fixture_app(monkeypatch, forbidden)
    monkeypatch.setenv("STOCK_COMP_SUPABASE_SECRET_KEY", "private-invalid-key")
    monkeypatch.setattr("api.public_app.load_account_snapshot", forbidden)
    request = body()
    if endpoint == "/v1/plans/current":
        request.pop("request_id")
    with TestClient(app) as client:
        response = client.post(endpoint, json=request)
    assert response.status_code == 503
    assert "invalid format" in response.json()["detail"]
    assert "STOCK_COMP_SUPABASE_SERVICE_ROLE_KEY" in response.json()["detail"]
    assert "private-invalid-key" not in response.text
    assert not saved


def test_background_job_runs_real_consensus_builder_with_dated_prices(monkeypatch):
    import numpy as np
    import pandas as pd
    from api.plan_service import build_cloud_plan
    from paper_trading.trading_plan import CONSENSUS_MODE

    dates = pd.bdate_range(end=pd.Timestamp.now(tz="UTC").tz_localize(None).normalize() - pd.Timedelta(days=1), periods=300)

    def prices(tickers, start, end, **kwargs):
        data = {}
        for ticker in tickers:
            close = 70 * np.exp(np.arange(len(dates)) * (.001 if ticker in {"SPY", "QQQ"} else .002))
            data[ticker] = pd.DataFrame({"Open": close, "High": close * 1.01, "Low": close * .99,
                                          "Close": close, "Volume": 1000000}, index=dates).loc[start:end]
        return data, {}

    monkeypatch.setattr("data.market_data.load_market_data", prices)
    monkeypatch.setattr("api.plan_service.expanded_current_scanner_universe", lambda: ["AAPL", "MSFT"])
    app, saved = fixture_app(monkeypatch, build_cloud_plan)
    # Give the actual adapter the same normal account shape used in production.
    from api import public_app
    original_load = public_app.load_account_snapshot

    async def load(token):
        result = await original_load(token)
        result["account"]["starting_capital"] = 100000
        return result

    monkeypatch.setattr(public_app, "load_account_snapshot", load)
    with TestClient(app) as client:
        started = client.post("/v1/plan-jobs", json={**body(), "strategy": CONSENSUS_MODE, "universe_mode": "EXPANDED"})
        assert started.status_code == 202
        done = terminal(client, started.json()["id"])
        assert done["status"] == "SUCCEEDED", done
        assert len(saved) == 1
        payload = saved[0][1]
        assert payload["recommendations"]
        assert all(item["factor_details"]["Proposed Quantity"] > 0 for item in payload["recommendations"])
        assert payload["market_as_of"][:10] == str(dates[-1].date())
