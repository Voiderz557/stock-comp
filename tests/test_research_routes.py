"""Read-only route integration with isolated auth, storage and providers."""

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from threading import BoundedSemaphore, Event
from concurrent.futures import ThreadPoolExecutor

from fastapi import HTTPException
from fastapi.testclient import TestClient
import jwt
import pandas as pd
import pytest

from api import auth, external_research, journal_service, public_app, recommendation_journal
from api.auth import Principal, require_principal


ROUTES = ("/v1/research/company/AAPL", "/v1/recommendation-journal")


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.delenv("STOCK_COMP_API_DEMO_MODE", raising=False)
    monkeypatch.delenv("STOCK_COMP_SEC_USER_AGENT", raising=False)
    monkeypatch.setenv("STOCK_COMP_SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setattr(public_app, "cloud_repository_configured", lambda: True)
    monkeypatch.setattr(recommendation_journal, "_now", lambda: datetime(2026, 10, 23, 22, tzinfo=timezone.utc))
    external_research._cache.clear()

    def forbidden(*args, **kwargs):
        pytest.fail("Unexpected storage, market-data or network operation")

    # Permit only an explicitly mocked market loader, never live I/O or writes.
    for name in ("load_plan_history", "load_account_snapshot", "build_cloud_plan",
                 "save_current_trading_plan", "create_competition_account",
                 "update_competition_account_snapshot", "record_recommendation_paper_action",
                 "change_recommendation_status", "load_auto_long_plan", "load_vix_context"):
        monkeypatch.setattr(public_app, name, forbidden)
    monkeypatch.setattr("data.market_data.load_market_data", forbidden)
    monkeypatch.setattr(journal_service, "load_market_data", forbidden)
    monkeypatch.setattr(journal_service, "_LOAD_SLOT", BoundedSemaphore(1))
    monkeypatch.setattr(journal_service, "PRICE_LOAD_TIMEOUT_SECONDS", 1)
    monkeypatch.setattr(external_research.httpx, "AsyncClient", forbidden)


def client(principal=None):
    app = public_app.create_app()
    if principal is not None:
        app.dependency_overrides[require_principal] = lambda: principal
    return TestClient(app)


def signed_in(token="caller-token"):
    return client(Principal(user_id="caller", access_token=token))


@pytest.mark.parametrize("route", ROUTES)
def test_missing_auth_rejected_before_loading(route):
    response = client().get(route)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("route", ROUTES)
def test_invalid_auth_rejected(monkeypatch, route):
    def invalid(_url):
        raise jwt.InvalidTokenError("private signing error")
    monkeypatch.setattr(auth, "_jwk_client", invalid)
    response = client().get(route, headers={"Authorization": "Bearer private-token"})
    assert response.status_code == 401
    assert "private" not in response.text


@pytest.mark.parametrize("route", ROUTES)
def test_demo_is_not_authenticated_access(monkeypatch, route):
    monkeypatch.setenv("STOCK_COMP_API_DEMO_MODE", "true")
    assert client().get(route).status_code == 403


@pytest.mark.parametrize("route", ROUTES)
def test_principal_without_token_fails_closed(route):
    assert client(Principal(user_id="caller")).get(route).status_code == 401


def test_company_uses_real_service_with_explicit_unavailable_configuration():
    response = signed_in().get(ROUTES[0])
    assert response.status_code == 200
    result = response.json()
    assert result["ticker"] == "AAPL"
    assert result["items"] == []
    assert result["providers"][0]["reason"] == "missing_contact_user_agent"
    assert "caller-token" not in response.text


def test_company_integration_normalizes_and_filters_provider_metadata(monkeypatch):
    calls = []
    async def provider(ticker, agent):
        calls.append((ticker, agent))
        return [{"id": "sec:sample", "kind": "filing", "title": "Form 8-K",
                 "source": "SEC EDGAR", "url": "https://www.sec.gov/Archives/edgar/",
                 "published_at": "2026-01-01", "filing_date": "2026-01-01"},
                {"id": "sec:future", "published_at": "9999-01-01", "filing_date": "9999-01-01"}], "ok", "recent_filings_only"
    monkeypatch.setenv("STOCK_COMP_SEC_USER_AGENT", "Tests contact@example.test")
    monkeypatch.setattr(external_research, "_sec", provider)
    # Research needs authentication but no account-storage configuration.
    monkeypatch.setattr(public_app, "cloud_repository_configured", lambda: False)
    response = signed_in().get("/v1/research/company/BRK.B")
    assert response.status_code == 200
    assert response.json()["ticker"] == "BRK.B"
    assert len(response.json()["items"]) == 1
    assert calls == [("BRK.B", "Tests contact@example.test")]
    assert "caller-token" not in response.text


def test_invalid_company_ticker_uses_service_validation():
    response = signed_in().get("/v1/research/company/bad$ticker")
    assert response.status_code == 200
    assert response.json()["providers"][0]["reason"] == "invalid_ticker"


def test_company_route_deadline_cancels_provider(monkeypatch):
    cancelled = []
    async def slow(ticker):
        try:
            await asyncio.sleep(10)
        finally:
            cancelled.append(ticker)
    monkeypatch.setattr(public_app, "get_external_research", slow)
    monkeypatch.setattr(public_app, "RESEARCH_REQUEST_TIMEOUT_SECONDS", 0.01)
    response = signed_in().get(ROUTES[0])
    assert response.status_code == 503
    assert response.json() == {"detail": "Company research timed out."}
    assert cancelled == ["AAPL"]


@pytest.mark.parametrize("mode", ["loaded", "partial", "unavailable", "busy", "timed_out"])
def test_journal_real_wrapper_retains_saved_records_and_status(monkeypatch, mode):
    history = [{"id": "saved-plan", "generated_at": "2026-01-05T22:00:00Z",
                "market_as_of": "2026-01-05", "strategy": "original", "status": "REPLACED",
                "recommendations": [{"ticker": "AAPL", "action": "BUY", "status": "DISMISSED",
                                     "score": 12, "rank": 1, "reason": "saved reason"}]}]
    original = deepcopy(history)
    tokens = []
    async def load(token):
        tokens.append(token)
        return history
    monkeypatch.setattr(public_app, "load_plan_history", load)
    calls = []
    release = Event()
    def prices(tickers, start, end, **kwargs):
        deadline_check = kwargs.pop("status_callback")
        assert callable(deadline_check)
        deadline_check("DOWNLOADING", tickers[0])
        calls.append((tickers, start, end, kwargs))
        if mode == "timed_out":
            release.wait(5)
        if mode == "unavailable":
            raise RuntimeError("private-provider-secret")
        if mode != "loaded":
            return {}, {}
        dates = pd.bdate_range("2026-01-06", "2026-05-01")
        dates = dates.difference(pd.to_datetime(["2026-01-19", "2026-02-16", "2026-04-03"]))
        return {ticker: pd.DataFrame({"Close": range(100, 100 + len(dates))}, index=dates)
                for ticker in tickers}, {}
    monkeypatch.setattr(journal_service, "load_market_data", prices)
    if mode == "busy":
        journal_service._LOAD_SLOT.acquire()
    if mode == "timed_out":
        monkeypatch.setattr(journal_service, "PRICE_LOAD_TIMEOUT_SECONDS", 0.01)
    # Drain this test's worker before restoring monkeypatches, including timeout.
    with ThreadPoolExecutor(max_workers=1) as executor:
        monkeypatch.setattr(journal_service, "_EXECUTOR", executor)
        try:
            response = signed_in().get("/v1/recommendation-journal")
        finally:
            release.set()
            if mode == "busy":
                journal_service._LOAD_SLOT.release()
    assert response.status_code == 200
    result = response.json()
    assert tokens == ["caller-token"]
    assert result["asof"] == "2026-10-23"
    assert result["price_load_status"] == mode
    assert bool(result["warnings"]) == (mode != "loaded")
    assert result["source_history"] == original == history
    assert result["entries"][0]["recommendation"] == original[0]["recommendations"][0]
    for outcome in result["entries"][0]["outcomes"]:
        if mode == "loaded":
            assert outcome["status"] == "complete"
            assert outcome["stock_return"] is not None
            assert outcome["excess_return"] == 0
        else:
            assert outcome["status"] == "unavailable"
            assert outcome["null_reason"] == "missing_history"
            assert outcome["stock_return"] is outcome["spy_return"] is outcome["excess_return"] is None
    request = result["fetch_request"]
    assert calls == ([] if mode == "busy" else [(
        request["tickers"], request["start_date"], request["end_date"], {"max_workers": 1})])
    assert "caller-token" not in response.text
    assert "private-provider-secret" not in response.text


def test_journal_empty_history_default_cutoff(monkeypatch):
    async def load(token):
        return []
    monkeypatch.setattr(public_app, "load_plan_history", load)
    result = signed_in().get(ROUTES[1]).json()
    assert result["asof"] == "2026-10-23"
    assert result["entries"] == []
    assert result["fetch_request"] is None
    assert result["price_load_status"] == "not_needed"
    assert result["warnings"] == []


def test_journal_has_no_query_parameters():
    operation = public_app.create_app().openapi()["paths"][ROUTES[1]]["get"]
    assert not any(parameter["in"] == "query" for parameter in operation.get("parameters", []))


def test_journal_missing_storage_configuration(monkeypatch):
    monkeypatch.setattr(public_app, "cloud_repository_configured", lambda: False)
    assert signed_in().get(ROUTES[1]).status_code == 503


def test_journal_propagates_storage_denial(monkeypatch):
    async def denied(token):
        raise HTTPException(status_code=403, detail="Plan history access denied.")
    monkeypatch.setattr(public_app, "load_plan_history", denied)
    response = signed_in().get(ROUTES[1])
    assert response.status_code == 403
    assert response.json() == {"detail": "Plan history access denied."}
