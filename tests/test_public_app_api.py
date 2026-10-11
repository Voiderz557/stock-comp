from __future__ import annotations

from copy import deepcopy

from fastapi.testclient import TestClient

from api.auth import require_principal
from api.demo_data import demo_snapshot
from api.public_app import create_app


def test_api_fails_closed_without_auth_configuration(monkeypatch):
    monkeypatch.delenv("STOCK_COMP_API_DEMO_MODE", raising=False)
    monkeypatch.delenv("STOCK_COMP_SUPABASE_URL", raising=False)
    client = TestClient(create_app())

    response = client.get("/v1/snapshot")

    assert response.status_code == 503
    assert response.json() == {"detail": "Authentication is not configured."}
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["cache-control"] == "no-store"


def test_explicit_demo_mode_returns_structured_snapshot(monkeypatch):
    monkeypatch.setenv("STOCK_COMP_API_DEMO_MODE", "true")
    monkeypatch.delenv("STOCK_COMP_SUPABASE_URL", raising=False)
    client = TestClient(create_app())

    response = client.get("/v1/snapshot")

    assert response.status_code == 200
    payload = response.json()
    assert payload["data_mode"] == "demo"
    assert payload["portfolio"]["cash"] == 80503.68
    assert payload["plan"]["status"] == "CURRENT"
    assert {item["action"] for item in payload["recommendations"]} == {"BUY", "WAIT", "AVOID"}


def test_authenticated_snapshot_does_not_present_replaced_plan_regime_as_current(monkeypatch):
    monkeypatch.setenv("STOCK_COMP_SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("STOCK_COMP_SUPABASE_PUBLISHABLE_KEY", "publishable-test-key")
    old = demo_snapshot()
    old["plan"]["status"] = "REPLACED"

    async def fake_load(_token, _account_id=None):
        return old

    monkeypatch.setattr("api.public_app.load_account_snapshot", fake_load)
    application = create_app()
    application.dependency_overrides[require_principal] = lambda: type(
        "VerifiedPrincipal", (),
        {"user_id": "user-123", "email": None, "is_demo": False, "access_token": "verified-token"},
    )()

    payload = TestClient(application).get("/v1/snapshot").json()

    assert payload["plan"]["status"] == "REPLACED"
    assert payload["recommendations"] == []
    assert payload["regime"]["name"] == "UNAVAILABLE"
    assert old["regime"]["name"] == "WEAK_UPTREND"


def test_snapshot_does_not_expose_secrets_or_local_paths(monkeypatch):
    monkeypatch.setenv("STOCK_COMP_API_DEMO_MODE", "1")
    payload_text = TestClient(create_app()).get("/v1/snapshot").text.lower()

    for forbidden in ("service_role", "api_key", "access_token", "refresh_token", "sqlite3", "c:\\\\users"):
        assert forbidden not in payload_text


def test_demo_fixture_is_deterministic_and_isolated():
    first = demo_snapshot()
    expected = deepcopy(first)
    first["positions"][0]["quantity"] = 999

    assert demo_snapshot() == expected


def test_authenticated_request_does_not_receive_demo_portfolio(monkeypatch):
    monkeypatch.delenv("STOCK_COMP_API_DEMO_MODE", raising=False)
    application = create_app()
    application.dependency_overrides[require_principal] = lambda: type(
        "VerifiedPrincipal",
        (),
        {"user_id": "user-123", "email": "test@example.invalid", "is_demo": False, "access_token": "test-token"},
    )()

    response = TestClient(application).get("/v1/snapshot")

    assert response.status_code == 503
    assert response.json() == {"detail": "Authenticated account storage is not configured yet."}


def test_demo_access_cannot_create_account(monkeypatch):
    monkeypatch.setenv("STOCK_COMP_API_DEMO_MODE", "true")
    response = TestClient(create_app()).post(
        "/v1/accounts",
        json={
            "name": "Competition account",
            "starting_capital": 100000,
            "cash": 100000,
            "snapshot_as_of": "2026-09-26T12:00:00Z",
        },
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "Demo access cannot create accounts."}


def test_authenticated_account_creation_forwards_verified_identity(monkeypatch):
    monkeypatch.setenv("STOCK_COMP_SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("STOCK_COMP_SUPABASE_PUBLISHABLE_KEY", "publishable-test-key")
    expected = demo_snapshot()
    captured = {}

    async def fake_create(token, **account):
        captured.update({"token": token, **account})
        return expected

    monkeypatch.setattr("api.public_app.create_competition_account", fake_create)
    application = create_app()
    application.dependency_overrides[require_principal] = lambda: type(
        "VerifiedPrincipal",
        (),
        {"user_id": "user-123", "email": None, "is_demo": False, "access_token": "verified-token"},
    )()

    response = TestClient(application).post(
        "/v1/accounts",
        json={
            "name": "  My   competition  ",
            "starting_capital": 100000,
            "cash": 97500,
            "snapshot_as_of": "2026-09-26T12:00:00Z",
        },
    )

    assert response.status_code == 201
    assert response.json() == expected
    assert captured["token"] == "verified-token"
    assert captured["name"] == "My competition"
    assert captured["starting_capital"] == 100000
    assert captured["cash"] == 97500


def test_wildcard_cors_is_rejected(monkeypatch):
    monkeypatch.setenv("STOCK_COMP_ALLOWED_ORIGINS", "*")

    try:
        create_app()
    except RuntimeError as error:
        assert "wildcard CORS is forbidden" in str(error)
    else:
        raise AssertionError("Wildcard CORS configuration was accepted")


def test_non_https_public_cors_origin_is_rejected(monkeypatch):
    monkeypatch.setenv("STOCK_COMP_ALLOWED_ORIGINS", "http://example.com")

    try:
        create_app()
    except RuntimeError as error:
        assert "must use HTTPS" in str(error)
    else:
        raise AssertionError("Insecure public CORS origin was accepted")


def test_write_rate_limit_returns_429(monkeypatch):
    monkeypatch.setenv("STOCK_COMP_API_DEMO_MODE", "true")
    monkeypatch.setenv("STOCK_COMP_WRITE_RATE_LIMIT_PER_MINUTE", "1")
    client = TestClient(create_app())
    payload = {
        "name": "Competition account",
        "starting_capital": 100000,
        "cash": 100000,
        "snapshot_as_of": "2026-09-26T12:00:00Z",
    }

    assert client.post("/v1/accounts", json=payload).status_code == 403
    limited = client.post("/v1/accounts", json=payload)

    assert limited.status_code == 429
    assert limited.json() == {"detail": "Too many requests. Try again later."}
    assert int(limited.headers["retry-after"]) > 0
    assert limited.headers["x-content-type-options"] == "nosniff"


def test_blank_account_name_is_rejected(monkeypatch):
    monkeypatch.setenv("STOCK_COMP_API_DEMO_MODE", "true")
    response = TestClient(create_app()).post(
        "/v1/accounts",
        json={
            "name": "   ",
            "starting_capital": 100000,
            "cash": 100000,
            "snapshot_as_of": "2026-09-26T12:00:00Z",
        },
    )

    assert response.status_code == 422


def test_account_update_normalizes_holdings_and_forwards_snapshot(monkeypatch):
    monkeypatch.setenv("STOCK_COMP_SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("STOCK_COMP_SUPABASE_PUBLISHABLE_KEY", "publishable-test-key")
    captured = {}

    async def fake_update(token, **account):
        captured.update({"token": token, **account})
        return demo_snapshot()

    monkeypatch.setattr("api.public_app.update_competition_account_snapshot", fake_update)
    application = create_app()
    application.dependency_overrides[require_principal] = lambda: type(
        "VerifiedPrincipal", (), {"user_id": "user-123", "email": None, "is_demo": False, "access_token": "verified-token"}
    )()
    response = TestClient(application).put("/v1/accounts/current", json={
        "name": "  Main  account ", "starting_capital": 100000, "cash": 80000,
        "snapshot_as_of": "2026-09-29T12:00:00Z",
        "expected_portfolio_version": 4,
        "positions": [{"ticker": "aapl", "side": "long", "quantity": 10, "average_price": 200}],
    })

    assert response.status_code == 200
    assert captured["token"] == "verified-token"
    assert captured["name"] == "Main account"
    assert captured["positions"][0]["ticker"] == "AAPL"
    assert captured["positions"][0]["side"] == "LONG"
    assert captured["expected_portfolio_version"] == 4


def test_account_update_rejects_duplicate_holdings(monkeypatch):
    monkeypatch.setenv("STOCK_COMP_API_DEMO_MODE", "true")
    payload = {
        "name": "Account", "starting_capital": 100000, "cash": 80000,
        "snapshot_as_of": "2026-09-29T12:00:00Z",
        "expected_portfolio_version": 1,
        "positions": [
            {"ticker": "AAPL", "side": "LONG", "quantity": 10, "average_price": 200},
            {"ticker": "aapl", "side": "long", "quantity": 5, "exact_cost_basis": 1000},
        ],
    }
    response = TestClient(create_app()).put("/v1/accounts/current", json=payload)

    assert response.status_code == 422


def test_account_update_requires_cost_information(monkeypatch):
    monkeypatch.setenv("STOCK_COMP_API_DEMO_MODE", "true")
    response = TestClient(create_app()).put("/v1/accounts/current", json={
        "name": "Account", "starting_capital": 100000, "cash": 80000,
        "snapshot_as_of": "2026-09-29T12:00:00Z",
        "expected_portfolio_version": 1,
        "positions": [{"ticker": "AAPL", "side": "LONG", "quantity": 10}],
    })

    assert response.status_code == 422


def test_account_update_requires_portfolio_version(monkeypatch):
    monkeypatch.setenv("STOCK_COMP_API_DEMO_MODE", "true")
    response = TestClient(create_app()).put("/v1/accounts/current", json={
        "name": "Account", "starting_capital": 100000, "cash": 80000,
        "snapshot_as_of": "2026-09-29T12:00:00Z", "positions": [],
    })

    assert response.status_code == 422


def test_authenticated_plan_generation_uses_existing_engine_and_saves(monkeypatch):
    monkeypatch.setenv("STOCK_COMP_SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("STOCK_COMP_SUPABASE_PUBLISHABLE_KEY", "publishable-test-key")
    monkeypatch.setenv("STOCK_COMP_SUPABASE_SECRET_KEY", "sb_secret_server-only-test-key")
    current = demo_snapshot()
    current["account"] = {"starting_capital": 100000, "portfolio_version": 2, "snapshot_as_of": "2026-09-30T12:00:00Z", "name": "Test"}
    captured = {}

    async def fake_load(token, account_id=None):
        return current

    def fake_build(snapshot, strategy, target_position_value, minimum_price, universe_mode):
        captured["strategy"] = strategy
        captured["target_position_value"] = target_position_value
        captured["minimum_price"] = minimum_price
        captured["universe_mode"] = universe_mode
        return {"strategy_name": strategy, "price_session_end": "2026-09-30", "market_data_as_of": "2026-09-30T00:00:00+00:00", "buys": [], "holdings": [], "data_warnings": [], "regime_result": None, "no_purchase_explanation": "No candidates."}

    async def fake_save(token, payload, *, user_id):
        captured["token"] = token
        captured["payload"] = payload
        captured["user_id"] = user_id
        saved = deepcopy(current)
        saved["plan"]["status"] = "CURRENT"
        return saved

    monkeypatch.setattr("api.public_app.load_account_snapshot", fake_load)
    monkeypatch.setattr("api.public_app.build_cloud_plan", fake_build)
    monkeypatch.setattr("api.public_app.save_current_trading_plan", fake_save)
    application = create_app()
    application.dependency_overrides[require_principal] = lambda: type(
        "VerifiedPrincipal", (), {"user_id": "user-123", "email": None, "is_demo": False, "access_token": "verified-token"}
    )()

    response = TestClient(application).post("/v1/plans/current", json={"strategy": "Momentum V2", "minimum_price": 12.50})

    assert response.status_code == 200
    assert captured["strategy"] == "Momentum V2"
    assert captured["target_position_value"] == 10_000
    assert captured["minimum_price"] == 12.50
    assert captured["universe_mode"] == "EXPANDED"
    assert captured["token"] == "verified-token"
    assert captured["user_id"] == "user-123"
    assert captured["payload"]["expected_portfolio_version"] == 2

    broad_response = TestClient(application).post("/v1/plans/current", json={
        "strategy": "Momentum V2", "universe_mode": "BROAD",
    })
    assert broad_response.status_code == 200
    assert captured["universe_mode"] == "BROAD"


def test_missing_trusted_plan_writer_fails_before_market_scan(monkeypatch):
    monkeypatch.setenv("STOCK_COMP_SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("STOCK_COMP_SUPABASE_PUBLISHABLE_KEY", "publishable-test-key")
    monkeypatch.delenv("STOCK_COMP_SUPABASE_SECRET_KEY", raising=False)
    monkeypatch.delenv("STOCK_COMP_SUPABASE_SERVICE_ROLE_KEY", raising=False)
    monkeypatch.setattr("api.public_app.load_account_snapshot", lambda *_args: (_ for _ in ()).throw(AssertionError("account read started")))
    application = create_app()
    application.dependency_overrides[require_principal] = lambda: type(
        "VerifiedPrincipal", (), {"user_id": "11111111-1111-1111-1111-111111111111", "email": None,
                                   "is_demo": False, "access_token": "verified-token"}
    )()
    response = TestClient(application).post("/v1/plans/current", json={"strategy": "Momentum V2"})
    assert response.status_code == 503
    assert "No plan was generated or saved" in response.json()["detail"]


def test_timed_out_live_scan_cannot_save_a_plan_after_browser_gives_up(monkeypatch):
    from threading import Event
    from time import sleep

    monkeypatch.setenv("STOCK_COMP_SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("STOCK_COMP_SUPABASE_PUBLISHABLE_KEY", "publishable-test-key")
    monkeypatch.setenv("STOCK_COMP_SUPABASE_SECRET_KEY", "sb_secret_server-only-test-key")
    monkeypatch.setattr("api.public_app.LIVE_PLAN_BUILD_TIMEOUT_SECONDS", 0.01)
    started, release = Event(), Event()
    saved = []

    async def fake_load(_token, account_id=None):
        return demo_snapshot()

    def slow_build(*_args):
        started.set()
        release.wait(1)
        return {"market_data_as_of": "2026-10-08", "buys": [], "holdings": []}

    async def fake_save(*_args):
        saved.append(True)
        return demo_snapshot()

    monkeypatch.setattr("api.public_app.load_account_snapshot", fake_load)
    monkeypatch.setattr("api.public_app.build_cloud_plan", slow_build)
    monkeypatch.setattr("api.public_app.save_current_trading_plan", fake_save)
    application = create_app()
    application.dependency_overrides[require_principal] = lambda: type(
        "VerifiedPrincipal", (), {"user_id": "user-123", "email": None, "is_demo": False, "access_token": "verified-token"}
    )()
    try:
        response = TestClient(application).post("/v1/plans/current", json={"strategy": "Momentum V2"})
        assert started.is_set()
        assert response.status_code == 504
        assert "No new plan was saved" in response.json()["detail"]
    finally:
        release.set()
    sleep(0.02)
    assert saved == []


def test_plan_generation_rejects_unknown_strategy(monkeypatch):
    monkeypatch.setenv("STOCK_COMP_API_DEMO_MODE", "true")
    response = TestClient(create_app()).post("/v1/plans/current", json={"strategy": "Made Up Strategy"})
    assert response.status_code == 422


def test_plan_generation_rejects_unknown_universe_mode(monkeypatch):
    monkeypatch.setenv("STOCK_COMP_API_DEMO_MODE", "true")
    response = TestClient(create_app()).post("/v1/plans/current", json={
        "strategy": "Momentum V2", "universe_mode": "MADE_UP",
    })
    assert response.status_code == 422


def test_plan_generation_rejects_oversize_or_nonfinite_target(monkeypatch):
    monkeypatch.setenv("STOCK_COMP_API_DEMO_MODE", "true")
    client = TestClient(create_app())
    for target in (0, 20_001, "NaN"):
        response = client.post(
            "/v1/plans/current",
            json={"strategy": "All strategies — consensus", "target_position_value": target},
        )
        assert response.status_code == 422


def test_plan_generation_accepts_research_floor_but_rejects_negative_or_nonfinite(monkeypatch):
    monkeypatch.setenv("STOCK_COMP_API_DEMO_MODE", "true")
    client = TestClient(create_app())
    for minimum in (-0.01, "NaN"):
        response = client.post("/v1/plans/current", json={
            "strategy": "All strategies — consensus", "minimum_price": minimum,
        })
        assert response.status_code == 422


def test_vix_endpoint_is_read_only_and_authenticated(monkeypatch):
    monkeypatch.setenv("STOCK_COMP_API_DEMO_MODE", "true")
    monkeypatch.setattr("api.public_app.load_vix_context", lambda: {
        "symbol": "VIX", "value": 19.5, "price_as_of": "2026-10-08",
        "status": "CURRENT", "warning": None,
    })
    response = TestClient(create_app()).get("/v1/market/vix")
    assert response.status_code == 200
    assert response.json()["value"] == 19.5


def test_demo_cannot_generate_cloud_plan(monkeypatch):
    monkeypatch.setenv("STOCK_COMP_API_DEMO_MODE", "true")
    response = TestClient(create_app()).post("/v1/plans/current", json={"strategy": "Momentum V2"})
    assert response.status_code == 403


def test_authenticated_history_is_scoped_to_verified_token(monkeypatch):
    monkeypatch.setenv("STOCK_COMP_SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("STOCK_COMP_SUPABASE_PUBLISHABLE_KEY", "publishable-test-key")
    captured = {}

    async def fake_history(token):
        captured["token"] = token
        return [{"id": "plan-1", "recommendations": []}]

    monkeypatch.setattr("api.public_app.load_plan_history", fake_history)
    application = create_app()
    application.dependency_overrides[require_principal] = lambda: type(
        "VerifiedPrincipal", (), {"user_id": "user-123", "is_demo": False, "access_token": "verified-token"}
    )()
    response = TestClient(application).get("/v1/plans/history")
    assert response.status_code == 200
    assert captured["token"] == "verified-token"
    assert response.json()[0]["id"] == "plan-1"


def test_recommendation_event_rejects_unknown_type_and_demo(monkeypatch):
    monkeypatch.setenv("STOCK_COMP_API_DEMO_MODE", "true")
    client = TestClient(create_app())
    recommendation_id = "11111111-1111-1111-1111-111111111111"
    assert client.post(f"/v1/recommendations/{recommendation_id}/events", json={"event_type": "RECORDED"}).status_code == 422
    assert client.post(f"/v1/recommendations/{recommendation_id}/events", json={"event_type": "DISMISSED"}).status_code == 403
