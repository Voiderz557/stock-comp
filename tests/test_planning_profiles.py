"""Isolated profile rules and TradingView-style advisory calculations."""

import asyncio
from pathlib import Path
from uuid import UUID

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from api import cloud_repository
from api.planning_profiles import (
    PlanningProfileWrite,
    TradingViewEstimateRequest,
    estimate_long_position,
)
from api.public_app import create_app
from api.auth import Principal, require_principal


def test_competition_profile_cannot_override_official_position_cap():
    accepted = PlanningProfileWrite.model_validate({
        "name": "  My  competition  plan  ", "mode": "COMPETITION",
        "rules": {"strategy": "All strategies — consensus", "target_position_value": 5_000},
    })
    assert accepted.name == "My competition plan"
    assert accepted.rules["target_position_value"] == 5_000
    with pytest.raises(ValidationError):
        PlanningProfileWrite.model_validate({
            "name": "Invalid", "mode": "COMPETITION",
            "rules": {"strategy": "All strategies — consensus", "target_position_value": 20_001},
        })
    with pytest.raises(ValidationError):
        PlanningProfileWrite.model_validate({
            "name": "Invalid", "mode": "COMPETITION",
            "rules": {"strategy": "All strategies — consensus", "target_position_value": 5_000, "disable_cap": True},
        })


def test_account_settings_validate_optional_exits_and_custom_price_floor():
    saved = PlanningProfileWrite.model_validate({
        "name": "App settings", "mode": "COMPETITION",
        "rules": {"strategy": "All strategies — consensus", "target_position_value": 10_000,
                  "minimum_price_mode": "CUSTOM", "minimum_price": 12.50,
                  "stop_loss_enabled": True, "take_profit_enabled": False},
    })
    assert saved.rules["minimum_price"] == 12.50
    assert saved.rules["stop_loss_enabled"] is True
    for invalid in (0, -1, float("nan")):
        with pytest.raises(ValidationError):
            PlanningProfileWrite.model_validate({
                "name": "Bad", "mode": "COMPETITION",
                "rules": {**saved.rules, "minimum_price": invalid},
            })

    research = PlanningProfileWrite.model_validate({
        "name": "App settings", "mode": "COMPETITION",
        "rules": {"strategy": "All strategies — consensus", "minimum_price_mode": "NO_MINIMUM", "minimum_price": 0},
    })
    assert research.rules["minimum_price"] == 0
    assert research.rules["minimum_price_mode"] == "NO_MINIMUM"


def test_tradingview_profile_rules_are_separate_and_validated():
    saved = PlanningProfileWrite.model_validate({
        "name": "Manual paper planner", "mode": "TRADINGVIEW",
        "rules": {
            "max_position_value": 30_000, "risk_per_trade_pct": 1,
            "stop_loss_pct": 5, "take_profit_pct": 10,
        },
    })
    assert saved.rules["max_position_value"] == 30_000
    assert "strategy" not in saved.rules
    with pytest.raises(ValidationError):
        PlanningProfileWrite.model_validate({
            "name": "Invalid", "mode": "TRADINGVIEW",
            "rules": {**saved.rules, "stop_loss_pct": 100},
        })


def test_position_estimate_is_cash_risk_and_cap_bounded():
    request = TradingViewEstimateRequest.model_validate({
        "entry_price": 100, "available_cash": 8_500, "account_equity": 100_000,
        "rules": {"max_position_value": 20_000, "risk_per_trade_pct": 1, "stop_loss_pct": 5, "take_profit_pct": 10},
    })
    result = estimate_long_position(request)
    assert result["shares"] == 85  # Cash binds before the 200-share risk/cap limits.
    assert result["cash_remaining"] == 0
    assert result["stop_watch_price"] == 95
    assert result["take_profit_watch_price"] == 110
    assert result["planned_risk_at_stop"] <= result["risk_budget"]
    assert "not TradingView orders" in result["warning"]
    assert estimate_long_position(request.model_copy(update={"available_cash": 0}))["shares"] == 0


def test_estimate_rejects_invalid_or_nonfinite_inputs(monkeypatch):
    monkeypatch.setenv("STOCK_COMP_API_DEMO_MODE", "true")
    client = TestClient(create_app())
    payload = {
        "entry_price": 100, "available_cash": 5000, "account_equity": 100000,
        "rules": {"max_position_value": 20000, "risk_per_trade_pct": 1, "stop_loss_pct": 5, "take_profit_pct": 10},
    }
    for invalid in (0, -1, "NaN"):
        response = client.post("/v1/planning-profiles/estimate", json={**payload, "entry_price": invalid})
        assert response.status_code == 422


def test_demo_can_estimate_but_cannot_save_profiles(monkeypatch):
    monkeypatch.setenv("STOCK_COMP_API_DEMO_MODE", "true")
    client = TestClient(create_app())
    assert client.get("/v1/planning-profiles").json() == []
    response = client.post("/v1/planning-profiles", json={
        "name": "Test", "mode": "COMPETITION", "rules": {"target_position_value": 5000},
    })
    assert response.status_code == 403


def test_authenticated_profile_create_and_update_do_not_touch_competition_account(monkeypatch):
    application = create_app()
    application.dependency_overrides[require_principal] = lambda: Principal(
        user_id="user-123", email=None, is_demo=False, access_token="verified-jwt"
    )
    calls = []

    async def create(token, payload):
        calls.append(("create", token, payload))
        return {"id": "11111111-1111-1111-1111-111111111111", **payload, "updated_at": "2026-10-07T12:00:00Z"}

    async def update(token, profile_id, payload, expected):
        calls.append(("update", token, payload, expected))
        return {"id": str(profile_id), **payload, "updated_at": "2026-10-07T13:00:00Z"}

    def no_account_write(*args, **kwargs):
        raise AssertionError("Competition account must not be written by profile endpoints")

    monkeypatch.setattr("api.public_app.create_planning_profile", create)
    monkeypatch.setattr("api.public_app.update_planning_profile", update)
    monkeypatch.setattr("api.public_app.update_competition_account_snapshot", no_account_write)
    client = TestClient(application)
    data = {"name": "Manual planner", "mode": "TRADINGVIEW", "rules": {
        "max_position_value": 30000, "risk_per_trade_pct": 1,
        "stop_loss_pct": 5, "take_profit_pct": 10,
    }}
    created = client.post("/v1/planning-profiles", json=data)
    updated = client.put(
        "/v1/planning-profiles/11111111-1111-1111-1111-111111111111",
        json={**data, "expected_updated_at": "2026-10-07T12:00:00Z"},
    )
    assert created.status_code == 201
    assert updated.status_code == 200
    assert [entry[0] for entry in calls] == ["create", "update"]
    assert all(entry[1] == "verified-jwt" for entry in calls)


def test_repository_scopes_table_request_with_user_token_and_version(monkeypatch):
    monkeypatch.setenv("STOCK_COMP_SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("STOCK_COMP_SUPABASE_PUBLISHABLE_KEY", "publishable-test-key")
    calls = []

    class FakeResponse:
        status_code = 200
        is_error = False

        def json(self):
            return [{"id": "profile-1", "updated_at": "2026-10-07T00:00:00+00:00"}]

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def request(self, method, url, **kwargs):
            calls.append((method, url, kwargs))
            return FakeResponse()

    monkeypatch.setattr(cloud_repository.httpx, "AsyncClient", FakeClient)
    asyncio.run(cloud_repository.list_planning_profiles("user-jwt"))
    asyncio.run(cloud_repository.update_planning_profile(
        "user-jwt", UUID("11111111-1111-1111-1111-111111111111"),
        {"name": "My rules", "mode": "TRADINGVIEW", "rules": {}},
        "2026-10-07T00:00:00+00:00",
    ))
    assert calls[0][0] == "GET"
    assert calls[1][0] == "PATCH"
    assert calls[1][2]["params"]["updated_at"] == "eq.2026-10-07T00:00:00+00:00"
    assert calls[0][2]["headers"]["Authorization"] == "Bearer user-jwt"
    assert "service" not in " ".join(calls[0][2]["headers"].values()).lower()


def test_profile_update_fails_closed_on_missing_or_changed_row(monkeypatch):
    monkeypatch.setattr(cloud_repository, "_call_profile_table", lambda *args, **kwargs: _empty())
    with pytest.raises(HTTPException) as exc:
        asyncio.run(cloud_repository.update_planning_profile(
            "token", UUID("11111111-1111-1111-1111-111111111111"), {}, "2026-10-07T00:00:00Z"
        ))
    assert exc.value.status_code == 409


async def _empty():
    return []


def test_migration_has_user_scoped_rls_and_no_account_mutation():
    migration = (Path(__file__).parents[1] / "supabase" / "migrations" / "20261007120000_planning_profiles.sql").read_text()
    assert "enable row level security" in migration
    assert "planning_profiles_owner_select" in migration
    assert "planning_profiles_owner_insert" in migration
    assert "planning_profiles_owner_update" in migration
    assert "auth.uid()" in migration
    assert "competition_accounts" not in migration
