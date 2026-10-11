from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from api import account_export
from api.public_app import create_app


class Response:
    def __init__(self, data, status_code=200):
        self.data = data
        self.status_code = status_code
        self.is_error = status_code >= 400

    def json(self):
        return self.data


def test_export_paginates_every_rls_table_and_omits_user_id(monkeypatch):
    monkeypatch.setenv("STOCK_COMP_SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("STOCK_COMP_SUPABASE_PUBLISHABLE_KEY", "publishable-key")
    monkeypatch.setattr(account_export, "_PAGE_SIZE", 2)
    calls = []

    class Client:
        def __init__(self, **_kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *_args): return None
        async def get(self, url, *, headers, params):
            calls.append((url, headers, params))
            table = url.rsplit("/", 1)[-1]
            if table == "positions" and params["offset"] == "0":
                return Response([{"id": "a", "user_id": "private"}, {"id": "b", "user_id": "private"}])
            if table == "positions" and params["offset"] == "2":
                return Response([{"id": "c", "user_id": "private"}])
            return Response([])

    monkeypatch.setattr(account_export.httpx, "AsyncClient", Client)
    exported = asyncio.run(account_export.export_owned_account_data("verified-token"))

    assert exported["counts"]["positions"] == 3
    assert exported["tables"]["positions"] == [{"id": "a"}, {"id": "b"}, {"id": "c"}]
    assert set(exported["tables"]) == set(account_export._TABLE_ORDER)
    assert len(calls) == len(account_export._TABLE_ORDER) + 1
    assert all(headers["Authorization"] == "Bearer verified-token" for _, headers, _ in calls)
    assert all(headers["apikey"] == "publishable-key" for _, headers, _ in calls)
    assert all("service" not in str(headers).lower() for _, headers, _ in calls)


def test_export_fails_closed_instead_of_returning_partial_tables(monkeypatch):
    monkeypatch.setenv("STOCK_COMP_SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("STOCK_COMP_SUPABASE_PUBLISHABLE_KEY", "publishable-key")

    class Client:
        def __init__(self, **_kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *_args): return None
        async def get(self, url, *, headers, params):
            return Response({"message": "missing table"}, 404) if url.endswith("/positions") else Response([])

    monkeypatch.setattr(account_export.httpx, "AsyncClient", Client)
    with pytest.raises(HTTPException) as error:
        asyncio.run(account_export.export_owned_account_data("verified-token"))
    assert error.value.status_code == 503


def test_demo_cannot_export_account(monkeypatch):
    monkeypatch.setenv("STOCK_COMP_API_DEMO_MODE", "true")
    response = TestClient(create_app()).get("/v1/account-export")
    assert response.status_code == 403


def test_authenticated_export_forwards_verified_token(monkeypatch):
    from api.auth import require_principal
    monkeypatch.setenv("STOCK_COMP_SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("STOCK_COMP_SUPABASE_PUBLISHABLE_KEY", "publishable-key")
    seen = []

    async def fake_export(token):
        seen.append(token)
        return {"tables": {}, "counts": {}, "schema_version": 1}

    monkeypatch.setattr("api.public_app.export_owned_account_data", fake_export)
    application = create_app()
    application.dependency_overrides[require_principal] = lambda: type(
        "Principal", (), {"is_demo": False, "access_token": "verified-token"}
    )()
    response = TestClient(application).get("/v1/account-export")
    assert response.status_code == 200
    assert seen == ["verified-token"]
    assert response.headers["cache-control"] == "no-store"
