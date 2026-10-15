from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi import HTTPException

from api import cloud_repository


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.is_error = status_code >= 400

    def json(self):
        return self._payload


class _FakeAsyncClient:
    def __init__(self, response, capture, **_kwargs):
        self._response = response
        self._capture = capture

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def post(self, url, headers, json):
        self._capture.update({"url": url, "headers": headers, "json": json})
        return self._response


def test_repository_forwards_user_token_without_service_role(monkeypatch):
    monkeypatch.setenv("STOCK_COMP_SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("STOCK_COMP_SUPABASE_PUBLISHABLE_KEY", "publishable-test-key")
    capture = {}
    response = _FakeResponse({"data_mode": "cloud", "positions": []})
    monkeypatch.setattr(
        cloud_repository.httpx,
        "AsyncClient",
        lambda **kwargs: _FakeAsyncClient(response, capture, **kwargs),
    )

    result = asyncio.run(cloud_repository.load_account_snapshot("user-jwt"))

    assert result["data_mode"] == "cloud"
    assert capture["headers"]["Authorization"] == "Bearer user-jwt"
    assert capture["headers"]["apikey"] == "publishable-test-key"
    assert "service" not in " ".join(capture["headers"].values()).lower()
    assert capture["json"] == {"p_account_id": None}


def test_repository_fails_closed_when_cloud_returns_no_account(monkeypatch):
    monkeypatch.setenv("STOCK_COMP_SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("STOCK_COMP_SUPABASE_PUBLISHABLE_KEY", "publishable-test-key")
    monkeypatch.setattr(
        cloud_repository.httpx,
        "AsyncClient",
        lambda **kwargs: _FakeAsyncClient(_FakeResponse(None), {}, **kwargs),
    )

    with pytest.raises(HTTPException) as error:
        asyncio.run(cloud_repository.load_account_snapshot("user-jwt"))

    assert error.value.status_code == 404
    assert error.value.detail == "No competition account exists yet."


def test_repository_maps_denied_access_without_leaking_provider_details(monkeypatch):
    monkeypatch.setenv("STOCK_COMP_SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("STOCK_COMP_SUPABASE_PUBLISHABLE_KEY", "publishable-test-key")
    monkeypatch.setattr(
        cloud_repository.httpx,
        "AsyncClient",
        lambda **kwargs: _FakeAsyncClient(_FakeResponse({"message": "provider detail"}, 403), {}, **kwargs),
    )

    with pytest.raises(HTTPException) as error:
        asyncio.run(cloud_repository.load_account_snapshot("user-jwt"))

    assert error.value.status_code == 403
    assert error.value.detail == "Account access was denied."


def test_create_account_uses_idempotent_rpc_then_loads_snapshot(monkeypatch):
    monkeypatch.setenv("STOCK_COMP_SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("STOCK_COMP_SUPABASE_PUBLISHABLE_KEY", "publishable-test-key")
    account_id = "11111111-1111-1111-1111-111111111111"
    responses = iter([
        _FakeResponse(account_id),
        _FakeResponse({"data_mode": "cloud", "positions": [], "portfolio": {"cash": 90000}}),
    ])
    calls = []

    class SequencedClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, headers, json):
            calls.append({"url": url, "headers": headers, "json": json})
            return next(responses)

    monkeypatch.setattr(cloud_repository.httpx, "AsyncClient", SequencedClient)

    result = asyncio.run(cloud_repository.create_competition_account(
        "user-jwt",
        name="Competition account",
        starting_capital=100000,
        cash=90000,
        snapshot_as_of="2026-09-26T12:00:00+00:00",
    ))

    assert result["portfolio"]["cash"] == 90000
    assert calls[0]["url"].endswith("/rpc/create_or_get_competition_account")
    assert calls[0]["json"]["p_name"] == "Competition account"
    assert calls[1]["url"].endswith("/rpc/get_current_account_snapshot")
    assert calls[1]["json"] == {"p_account_id": account_id}
    assert all(call["headers"]["Authorization"] == "Bearer user-jwt" for call in calls)


def test_update_account_uses_snapshot_rpc_then_reloads(monkeypatch):
    monkeypatch.setenv("STOCK_COMP_SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("STOCK_COMP_SUPABASE_PUBLISHABLE_KEY", "publishable-test-key")
    account_id = "11111111-1111-1111-1111-111111111111"
    responses = iter([_FakeResponse(account_id), _FakeResponse({"data_mode": "cloud", "positions": []})])
    calls = []

    class SequencedClient:
        def __init__(self, **_kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *_args): return None
        async def post(self, url, headers, json):
            calls.append({"url": url, "headers": headers, "json": json})
            return next(responses)

    monkeypatch.setattr(cloud_repository.httpx, "AsyncClient", SequencedClient)
    result = asyncio.run(cloud_repository.update_competition_account_snapshot(
        "user-jwt", name="Account", starting_capital=100000, cash=80000,
        snapshot_as_of="2026-09-29T12:00:00+00:00",
        positions=[{"ticker": "AAPL", "side": "LONG", "quantity": 10, "average_price": 200, "exact_cost_basis": None}],
        expected_portfolio_version=3,
    ))

    assert result["positions"] == []
    assert calls[0]["url"].endswith("/rpc/update_competition_account_snapshot")
    assert calls[0]["json"]["p_positions"][0]["ticker"] == "AAPL"
    assert calls[0]["json"]["p_expected_portfolio_version"] == 3
    assert calls[1]["json"] == {"p_account_id": account_id}


def test_repository_maps_portfolio_version_conflict(monkeypatch):
    monkeypatch.setenv("STOCK_COMP_SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("STOCK_COMP_SUPABASE_PUBLISHABLE_KEY", "publishable-test-key")
    monkeypatch.setattr(
        cloud_repository.httpx,
        "AsyncClient",
        lambda **kwargs: _FakeAsyncClient(_FakeResponse({"code": "40001"}, 400), {}, **kwargs),
    )

    with pytest.raises(HTTPException) as error:
        asyncio.run(cloud_repository.update_competition_account_snapshot(
            "user-jwt", name="Account", starting_capital=100000, cash=80000,
            snapshot_as_of="2026-09-29T12:00:00+00:00", positions=[],
            expected_portfolio_version=1,
        ))

    assert error.value.status_code == 409
    assert "Refresh" in error.value.detail


def test_save_plan_uses_server_only_rpc_then_user_scoped_reload(monkeypatch):
    monkeypatch.setenv("STOCK_COMP_SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("STOCK_COMP_SUPABASE_PUBLISHABLE_KEY", "publishable-test-key")
    monkeypatch.setenv("STOCK_COMP_SUPABASE_SECRET_KEY", "sb_secret_server-only-test-key")
    responses = iter([_FakeResponse("22222222-2222-2222-2222-222222222222"), _FakeResponse({"data_mode": "cloud", "plan": {"status": "CURRENT"}})])
    calls = []

    class SequencedClient:
        def __init__(self, **_kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *_args): return None
        async def post(self, url, headers, json):
            calls.append({"url": url, "headers": headers, "json": json})
            return next(responses)

    monkeypatch.setattr(cloud_repository.httpx, "AsyncClient", SequencedClient)
    result = asyncio.run(cloud_repository.save_current_trading_plan("user-jwt", {
        "expected_portfolio_version": 5,
        "strategy_name": "Momentum V2",
        "market_as_of": "2026-09-30T23:59:59Z",
        "summary": "One candidate.",
        "portfolio_snapshot": {"cash": 100000, "positions": []},
        "regime": {"name": "WEAK_UPTREND"},
        "warnings": [],
        "recommendations": [{"ticker": "AAPL"}],
    }, user_id="11111111-1111-1111-1111-111111111111"))

    assert result["plan"]["status"] == "CURRENT"
    assert calls[0]["url"].endswith("/rpc/save_generated_trading_plan")
    assert calls[0]["headers"]["apikey"] == "sb_secret_server-only-test-key"
    assert "Authorization" not in calls[0]["headers"]
    assert calls[0]["json"]["p_user_id"] == "11111111-1111-1111-1111-111111111111"
    assert calls[0]["json"]["p_expected_portfolio_version"] == 5
    assert calls[0]["json"]["p_recommendations"] == [{"ticker": "AAPL"}]
    assert calls[1]["url"].endswith("/rpc/get_current_account_snapshot")
    assert calls[1]["headers"]["Authorization"] == "Bearer user-jwt"


def test_plan_writer_fails_closed_without_backend_credential(monkeypatch):
    import pytest
    from fastapi import HTTPException

    monkeypatch.delenv("STOCK_COMP_SUPABASE_SECRET_KEY", raising=False)
    monkeypatch.delenv("STOCK_COMP_SUPABASE_SERVICE_ROLE_KEY", raising=False)
    with pytest.raises(HTTPException) as error:
        asyncio.run(cloud_repository.save_current_trading_plan(
            "user-jwt", {}, user_id="11111111-1111-1111-1111-111111111111"
        ))
    assert error.value.status_code == 503


@pytest.mark.parametrize("credential", ["invalid-private-value", "sb_secret_bad\nheader"])
def test_storage_preflight_rejects_bad_format_without_network_or_secret_output(monkeypatch, credential):
    monkeypatch.setenv("STOCK_COMP_SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("STOCK_COMP_SUPABASE_SECRET_KEY", credential)
    monkeypatch.setattr(cloud_repository.httpx, "AsyncClient", lambda **kw: pytest.fail("No network in preflight"))
    with pytest.raises(HTTPException) as error:
        cloud_repository.validate_trusted_plan_storage_configuration()
    assert error.value.status_code == 503
    assert credential not in error.value.detail


def test_storage_preflight_preserves_explicit_legacy_headers(monkeypatch):
    monkeypatch.setenv("STOCK_COMP_SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.delenv("STOCK_COMP_SUPABASE_SECRET_KEY", raising=False)
    monkeypatch.setenv("STOCK_COMP_SUPABASE_SERVICE_ROLE_KEY", "legacy-test-jwt")
    capture = {}
    monkeypatch.setattr(cloud_repository.httpx, "AsyncClient", lambda **kw: _FakeAsyncClient(
        _FakeResponse("22222222-2222-2222-2222-222222222222"), capture, **kw,
    ))
    cloud_repository.validate_trusted_plan_storage_configuration()
    asyncio.run(cloud_repository._call_trusted_plan_writer({}))
    assert capture["headers"]["apikey"] == "legacy-test-jwt"
    assert capture["headers"]["Authorization"] == "Bearer legacy-test-jwt"


def test_recommendation_status_event_uses_user_token_and_reload(monkeypatch):
    from uuid import UUID
    monkeypatch.setenv("STOCK_COMP_SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("STOCK_COMP_SUPABASE_PUBLISHABLE_KEY", "publishable-test-key")
    calls = []
    responses = iter([_FakeResponse("11111111-1111-1111-1111-111111111111"), _FakeResponse({"recommendations": []})])

    class SequencedClient:
        def __init__(self, **_kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *_args): return None
        async def post(self, url, headers, json):
            calls.append({"url": url, "headers": headers, "json": json})
            return next(responses)

    monkeypatch.setattr(cloud_repository.httpx, "AsyncClient", SequencedClient)
    recommendation_id = UUID("11111111-1111-1111-1111-111111111111")
    result = asyncio.run(cloud_repository.change_recommendation_status("user-jwt", recommendation_id, "DISMISSED"))
    assert result["recommendations"] == []
    assert calls[0]["url"].endswith("/rpc/set_recommendation_status")
    assert calls[0]["json"] == {"p_recommendation_id": str(recommendation_id), "p_event_type": "DISMISSED"}
    assert all(call["headers"]["Authorization"] == "Bearer user-jwt" for call in calls)
