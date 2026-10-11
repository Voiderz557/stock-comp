from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from fastapi import HTTPException

from api import cloud_repository


class Response:
    def __init__(self, data):
        self.data = data
        self.status_code = 200
        self.is_error = False

    def json(self):
        return self.data


def test_plan_history_attaches_only_rls_scoped_candidate_diagnostics(monkeypatch):
    monkeypatch.setenv("STOCK_COMP_SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("STOCK_COMP_SUPABASE_PUBLISHABLE_KEY", "publishable-key")
    plan_id = str(uuid4())
    history = [{"id": plan_id, "recommendations": []}]
    captures = []

    async def fake_rpc(token, name, body):
        assert token == "verified-token"
        assert name == "get_account_plan_history"
        return history

    class Client:
        def __init__(self, **_kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *_args): return None
        async def get(self, url, *, headers, params):
            captures.append((url, headers, params))
            return Response([{"id": plan_id, "portfolio_snapshot": {
                "cash": 100, "positions": [{"ticker": "PRIVATE"}],
                "candidate_diagnostics": {"AAPL": {"status": "NOT_SELECTED", "reason": "WAIT signal."}},
            }}])

    monkeypatch.setattr(cloud_repository, "_call_rpc", fake_rpc)
    monkeypatch.setattr(cloud_repository.httpx, "AsyncClient", Client)
    result = asyncio.run(cloud_repository.load_plan_history("verified-token"))
    assert result[0]["candidate_diagnostics"]["AAPL"]["reason"] == "WAIT signal."
    assert "positions" not in result[0]
    assert captures[0][1]["Authorization"] == "Bearer verified-token"
    assert captures[0][2]["id"] == f"in.({plan_id})"


def test_plan_history_fails_when_diagnostics_query_omits_a_plan(monkeypatch):
    monkeypatch.setenv("STOCK_COMP_SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("STOCK_COMP_SUPABASE_PUBLISHABLE_KEY", "publishable-key")
    plan_id = str(uuid4())

    async def fake_rpc(*_args):
        return [{"id": plan_id}]

    class Client:
        def __init__(self, **_kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *_args): return None
        async def get(self, *_args, **_kwargs): return Response([])

    monkeypatch.setattr(cloud_repository, "_call_rpc", fake_rpc)
    monkeypatch.setattr(cloud_repository.httpx, "AsyncClient", Client)
    with pytest.raises(HTTPException) as error:
        asyncio.run(cloud_repository.load_plan_history("verified-token"))
    assert error.value.status_code == 502
