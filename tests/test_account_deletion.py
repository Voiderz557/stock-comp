"""No real accounts or credentials: all provider requests use MockTransport."""
import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi import HTTPException

from api import account_deletion as deletion
from api.auth import Principal

OWNER = "11111111-1111-1111-1111-111111111111"
OTHER = "22222222-2222-2222-2222-222222222222"


@pytest.fixture
def provider(monkeypatch):
    deletion._challenges.clear()
    monkeypatch.setenv("STOCK_COMP_SUPABASE_URL", "https://test.example")
    monkeypatch.setenv("STOCK_COMP_SUPABASE_PUBLISHABLE_KEY", "sb_publishable_test")
    monkeypatch.setenv("STOCK_COMP_SUPABASE_SECRET_KEY", "sb_secret_test")
    state = {"requests": [], "id": OWNER, "age": 0, "status": 200, "timeout": False}
    def handle(request):
        state["requests"].append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"id": state["id"], "last_sign_in_at":
                (datetime.now(timezone.utc) - timedelta(seconds=state["age"])).isoformat()})
        if state["timeout"]:
            raise httpx.ReadTimeout("PRIVATE PROVIDER DETAILS", request=request)
        return httpx.Response(state["status"], json={"id": OWNER})
    client = httpx.AsyncClient
    monkeypatch.setattr(deletion.httpx, "AsyncClient", lambda **kw: client(transport=httpx.MockTransport(handle), **kw))
    async def export(token):
        assert token == "session"
        return {"exported_at": "2026-10-30T12:00:00Z", "counts": {"positions": 2}}
    monkeypatch.setattr(deletion, "export_owned_account_data", export)
    return state


def run(coro):
    return asyncio.run(coro)


def principal(owner=OWNER, token="session"):
    return Principal(owner, access_token=token)


def test_preview_is_read_only_and_confirm_is_self_only_once(provider):
    review = run(deletion.preview_deletion(principal()))
    assert review["counts"] == {"positions": 2}
    assert all(r.method == "GET" for r in provider["requests"])
    assert run(deletion.confirm_deletion(principal(), review["challenge"], deletion.CONFIRMATION, True))["deleted"]
    request = provider["requests"][-1]
    assert request.method == "DELETE" and request.url.path == f"/auth/v1/admin/users/{OWNER}"
    assert request.headers["apikey"] == "sb_secret_test"
    assert b'"should_soft_delete":false' in request.content
    with pytest.raises(HTTPException) as error:
        run(deletion.confirm_deletion(principal(), review["challenge"], deletion.CONFIRMATION, True))
    assert error.value.status_code == 409


@pytest.mark.parametrize("case", ["other_user", "new_token", "expired", "phrase", "ack"])
def test_invalid_confirmation_never_deletes(provider, case):
    review = run(deletion.preview_deletion(principal()))
    actor, phrase, ack = principal(), deletion.CONFIRMATION, True
    if case == "other_user": actor = principal(OTHER)
    if case == "new_token": actor = principal(token="refreshed")
    if case == "phrase": phrase = "delete"
    if case == "ack": ack = False
    if case == "expired":
        entry = deletion._challenges[review["challenge"]]
        deletion._challenges[review["challenge"]] = (*entry[:2], 0)
    with pytest.raises(HTTPException):
        run(deletion.confirm_deletion(actor, review["challenge"], phrase, ack))
    assert not any(r.method == "DELETE" for r in provider["requests"])


@pytest.mark.parametrize("age,identity", [(901, OWNER), (0, OTHER)])
def test_recent_online_identity_required(provider, age, identity):
    provider.update(age=age, id=identity)
    with pytest.raises(HTTPException) as error:
        run(deletion.preview_deletion(principal()))
    assert error.value.status_code == 401
    assert not deletion._challenges


@pytest.mark.parametrize("status,timeout", [(403, False), (500, False), (302, False), (200, True)])
def test_provider_failure_never_claims_success_or_retries(provider, status, timeout):
    review = run(deletion.preview_deletion(principal()))
    provider.update(status=status, timeout=timeout)
    with pytest.raises(HTTPException) as error:
        run(deletion.confirm_deletion(principal(), review["challenge"], deletion.CONFIRMATION, True))
    assert error.value.status_code == 503
    assert "PRIVATE" not in error.value.detail
    assert review["challenge"] not in deletion._challenges
    assert len([r for r in provider["requests"] if r.method == "DELETE"]) == 1


def test_demo_cannot_review_or_delete(provider):
    with pytest.raises(HTTPException):
        run(deletion.preview_deletion(Principal("demo", is_demo=True)))
    assert provider["requests"] == []


def test_api_rejects_client_supplied_owner_and_unauthenticated_call(provider):
    from fastapi.testclient import TestClient
    from api.public_app import create_app
    from api.auth import require_principal
    app = create_app()
    app.dependency_overrides[require_principal] = principal
    client = TestClient(app)
    response = client.post("/v1/account-deletion/confirm", json={"challenge": "x" * 32,
        "confirmation": deletion.CONFIRMATION, "acknowledged": True, "user_id": OTHER})
    assert response.status_code == 422
    app.dependency_overrides.clear()
    assert client.post("/v1/account-deletion/preview").status_code == 401
