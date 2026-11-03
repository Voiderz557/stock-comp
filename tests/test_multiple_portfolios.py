"""Portfolio scope must follow requests and async jobs, never another tab."""
import asyncio
from uuid import UUID

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from api import cloud_repository as repo, public_app
from api.auth import Principal, require_principal

A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"


def test_repository_scope_is_concurrent_and_explicit(monkeypatch):
    monkeypatch.setenv("STOCK_COMP_SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("STOCK_COMP_SUPABASE_PUBLISHABLE_KEY", "test")
    captured = []
    client_class = httpx.AsyncClient

    async def handler(request):
        await asyncio.sleep(0)
        captured.append((request.headers.get("x-portfolio-id"), request.read()))
        return httpx.Response(200, json={"account": {"id": request.headers.get("x-portfolio-id")}})

    monkeypatch.setattr(repo.httpx, "AsyncClient", lambda **kw: client_class(transport=httpx.MockTransport(handler), **kw))

    async def run():
        async def one(portfolio):
            scope = repo.selected_portfolio.set(portfolio)
            try:
                result = await repo.load_account_snapshot("user")
                assert result["account"]["id"] == portfolio
                with pytest.raises(HTTPException):
                    await repo.load_account_snapshot("user", UUID(B if portfolio == A else A))
            finally:
                repo.selected_portfolio.reset(scope)
        await asyncio.gather(one(A), one(B))
        assert repo.selected_portfolio.get() is None
    asyncio.run(run())
    assert {row[0] for row in captured} == {A, B}


def test_scope_header_validated_and_reset_across_requests(monkeypatch):
    app = public_app.create_app()
    app.dependency_overrides[require_principal] = lambda: Principal(A, access_token="test")
    async def portfolios(_token):
        return [{"id": repo.selected_portfolio.get()}]
    monkeypatch.setattr(public_app, "list_portfolios", portfolios)
    with TestClient(app) as client:
        assert client.get("/v1/portfolios", headers={"X-Portfolio-Id": "bad"}).status_code == 422
        assert client.get("/v1/portfolios", headers={"X-Portfolio-Id": A}).json()["portfolios"][0]["id"] == A
        assert client.get("/v1/portfolios").json()["portfolios"][0]["id"] is None


def test_new_portfolio_reload_uses_new_scope_and_restores_previous(monkeypatch):
    async def rpc(*args, **kw): return B
    async def snapshot(token, account_id):
        assert str(account_id) == B and repo.selected_portfolio.get() == B
        return {"account": {"id": B}}
    monkeypatch.setattr(repo, "_call_rpc", rpc)
    monkeypatch.setattr(repo, "load_account_snapshot", snapshot)
    async def run():
        scope = repo.selected_portfolio.set(A)
        try:
            assert (await repo.create_portfolio("user", {}))["account"]["id"] == B
            assert repo.selected_portfolio.get() == A
        finally: repo.selected_portfolio.reset(scope)
    asyncio.run(run())


def test_plan_writer_rejects_a_different_selected_portfolio(monkeypatch):
    monkeypatch.setattr(repo, "trusted_plan_storage_configured", lambda: True)
    async def forbidden(*args, **kwargs): pytest.fail("Must not save wrong portfolio")
    monkeypatch.setattr(repo, "_call_trusted_plan_writer", forbidden)
    async def run():
        scope = repo.selected_portfolio.set(A)
        try:
            with pytest.raises(HTTPException) as error:
                await repo.save_current_trading_plan("user", {"portfolio_snapshot": {"account_id": B}}, user_id=A)
            assert error.value.status_code == 409
        finally: repo.selected_portfolio.reset(scope)
    asyncio.run(run())


def test_background_scan_keeps_original_portfolio_after_switch(monkeypatch):
    from threading import Event
    from time import sleep
    from uuid import uuid4

    entered, release = Event(), Event()
    saved = []
    monkeypatch.setattr(public_app, "cloud_repository_configured", lambda: True)
    monkeypatch.setattr(public_app, "trusted_plan_storage_configured", lambda: True)
    monkeypatch.setattr(public_app, "validate_trusted_plan_storage_configuration", lambda: None)

    async def load(_token):
        return {"account": {"id": repo.selected_portfolio.get(), "portfolio_version": 1},
                "portfolio": {"cash": 100000}, "positions": [], "recommendations": [],
                "plan": {"status": "NONE"}}

    def build(*args, **kwargs):
        entered.set()
        release.wait(2)
        return {"strategy_name": "Momentum V2", "market_data_as_of": "2026-11-02T00:00:00Z",
                "buys": [], "holdings": []}

    async def save(_token, payload, *, user_id):
        saved.append((repo.selected_portfolio.get(), payload["portfolio_snapshot"]["account_id"]))
        return await load(_token)

    monkeypatch.setattr(public_app, "load_account_snapshot", load)
    monkeypatch.setattr(public_app, "build_cloud_plan", build)
    monkeypatch.setattr(public_app, "save_current_trading_plan", save)
    app = public_app.create_app()
    app.dependency_overrides[require_principal] = lambda: Principal(A, access_token="test")
    with TestClient(app) as client:
        try:
            response = client.post("/v1/plan-jobs", headers={"X-Portfolio-Id": A},
                json={"request_id": str(uuid4()), "strategy": "Momentum V2"})
            assert response.status_code == 202
            job_id = response.json()["id"]
            assert entered.wait(.5)
            assert client.get(f"/v1/plan-jobs/{job_id}", headers={"X-Portfolio-Id": B}).status_code == 404
        finally:
            release.set()
        for _ in range(100):
            result = client.get(f"/v1/plan-jobs/{job_id}", headers={"X-Portfolio-Id": A})
            if result.json()["status"] != "RUNNING": break
            sleep(.005)
        assert result.json()["status"] == "SUCCEEDED"
        assert saved == [(A, A)]


def test_sql_explicit_account_header_mismatch_maps_to_conflict(monkeypatch):
    monkeypatch.setenv("STOCK_COMP_SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("STOCK_COMP_SUPABASE_PUBLISHABLE_KEY", "test")
    client_class = httpx.AsyncClient

    async def handler(request):
        return httpx.Response(400, json={
            "code": "P0001",
            "message": "Explicit account and portfolio header disagree.",
        })

    monkeypatch.setattr(repo.httpx, "AsyncClient", lambda **kw: client_class(
        transport=httpx.MockTransport(handler), **kw))

    async def run():
        with pytest.raises(HTTPException) as error:
            await repo.load_account_snapshot("user")
        assert error.value.status_code == 409
        assert error.value.detail == "Select an available portfolio and refresh before continuing."

    asyncio.run(run())
