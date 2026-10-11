from __future__ import annotations

import asyncio
from uuid import UUID

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from api import cloud_repository
from api.auth import require_principal
from api.public_app import create_app


ACTION_ID = UUID("11111111-1111-1111-1111-111111111111")
RECOMMENDATION_ID = UUID("22222222-2222-2222-2222-222222222222")


def _authenticated_app(monkeypatch):
    monkeypatch.setenv("STOCK_COMP_SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("STOCK_COMP_SUPABASE_PUBLISHABLE_KEY", "publishable-key")
    app = create_app()
    app.dependency_overrides[require_principal] = lambda: type(
        "Principal", (), {"is_demo": False, "access_token": "verified-token"}
    )()
    return app


def test_demo_cannot_record_paper_action(monkeypatch):
    monkeypatch.setenv("STOCK_COMP_API_DEMO_MODE", "true")
    response = TestClient(create_app()).post("/v1/paper-actions", json={
        "recommendation_id": str(RECOMMENDATION_ID), "quantity": 1,
        "expected_portfolio_version": 1, "idempotency_key": "test-key-123",
    })
    assert response.status_code == 403


@pytest.mark.parametrize("quantity", [0, -1, 1.5, "nan"])
def test_paper_action_rejects_non_whole_or_invalid_quantity(monkeypatch, quantity):
    app = _authenticated_app(monkeypatch)
    response = TestClient(app).post("/v1/paper-actions", json={
        "recommendation_id": str(RECOMMENDATION_ID), "quantity": quantity,
        "expected_portfolio_version": 1, "idempotency_key": "test-key-123",
    })
    assert response.status_code == 422


def test_api_forwards_only_verified_paper_action_fields(monkeypatch):
    app = _authenticated_app(monkeypatch)
    captured = {}

    async def fake_record(token, **kwargs):
        captured.update(token=token, **kwargs)
        return {"action_id": str(ACTION_ID), "snapshot": {
            "plan": {"status": "STALE"},
            "regime": {"name": "STRONG_UPTREND"},
            "recommendations": [{"ticker": "AAPL"}],
        }}

    monkeypatch.setattr("api.public_app.record_recommendation_paper_action", fake_record)
    response = TestClient(app).post("/v1/paper-actions", json={
        "recommendation_id": str(RECOMMENDATION_ID), "quantity": 2,
        "expected_portfolio_version": 3, "idempotency_key": "test-key-123",
    })
    assert response.status_code == 200
    assert captured == {
        "token": "verified-token", "recommendation_id": RECOMMENDATION_ID,
        "quantity": 2, "expected_portfolio_version": 3, "idempotency_key": "test-key-123",
    }
    assert response.json()["snapshot"]["recommendations"] == []
    assert response.json()["snapshot"]["regime"]["name"] == "UNAVAILABLE"


def test_repository_records_one_rpc_action_then_loads_snapshot(monkeypatch):
    calls = []

    async def fake_rpc(token, name, body, **kwargs):
        calls.append((token, name, body, kwargs))
        return str(ACTION_ID) if name == "record_recommendation_paper_action" else {"plan": {"status": "STALE"}}

    monkeypatch.setattr(cloud_repository, "_call_rpc", fake_rpc)
    result = asyncio.run(cloud_repository.record_recommendation_paper_action(
        "verified-token", recommendation_id=RECOMMENDATION_ID, quantity=2,
        expected_portfolio_version=3, idempotency_key="test-key-123",
    ))
    assert result["action_id"] == str(ACTION_ID)
    assert calls[0][1] == "record_recommendation_paper_action"
    assert calls[0][2]["p_quantity"] == 2
    assert calls[0][2]["p_idempotency_key"] == "test-key-123"
    assert calls[0][3] == {"paper_action": True}
    assert calls[1][1] == "get_current_account_snapshot"


def test_missing_paper_action_migration_is_not_reported_as_success(monkeypatch):
    monkeypatch.setenv("STOCK_COMP_SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("STOCK_COMP_SUPABASE_PUBLISHABLE_KEY", "publishable-key")

    class Response:
        status_code = 404
        is_error = True
        def json(self): return {"code": "PGRST202"}

    class Client:
        def __init__(self, **_kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *_args): return None
        async def post(self, *_args, **_kwargs): return Response()

    monkeypatch.setattr(cloud_repository.httpx, "AsyncClient", Client)
    with pytest.raises(HTTPException) as error:
        asyncio.run(cloud_repository.load_recent_paper_actions("verified-token"))
    assert error.value.status_code == 503


def test_history_endpoint_uses_authenticated_user(monkeypatch):
    app = _authenticated_app(monkeypatch)
    seen = []

    async def fake_history(token):
        seen.append(token)
        return [{"ticker": "AAPL", "action_type": "OPEN"}]

    monkeypatch.setattr("api.public_app.load_recent_paper_actions", fake_history)
    response = TestClient(app).get("/v1/paper-actions")
    assert response.status_code == 200
    assert seen == ["verified-token"]
    assert response.json()[0]["ticker"] == "AAPL"
