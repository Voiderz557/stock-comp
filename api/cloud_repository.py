"""Supabase repository using caller JWTs for account access.

Only saving a generated plan uses a backend-only service-role credential: the
public plan writer is revoked so clients cannot forge strategy output. The
API supplies the user ID from a separately verified JWT; all other account
calls in this module retain the user token and RLS boundary. The separate
account_deletion module uses the server credential only after a fresh online
identity check and an explicit, single-use self-deletion confirmation.
"""

from __future__ import annotations

import os
from uuid import UUID

import httpx
from fastapi import HTTPException, status


def cloud_repository_configured() -> bool:
    return bool(
        (os.getenv("STOCK_COMP_SUPABASE_URL") or "").strip()
        and (os.getenv("STOCK_COMP_SUPABASE_PUBLISHABLE_KEY") or "").strip()
    )


def trusted_plan_storage_configured() -> bool:
    return cloud_repository_configured() and bool(
        (os.getenv("STOCK_COMP_SUPABASE_SECRET_KEY") or "").strip()
        or (os.getenv("STOCK_COMP_SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    )


def validate_trusted_plan_storage_configuration() -> None:
    """Check local configuration before scanning; never fetch or log credentials.

    This checks shape only, not whether Supabase accepts the credential. The
    authenticated, version-checked writer remains the authority for saving.
    """
    _trusted_plan_writer_headers()


def _trusted_plan_writer_headers() -> dict[str, str]:
    supabase_url = (os.getenv("STOCK_COMP_SUPABASE_URL") or "").strip()
    secret_key = (os.getenv("STOCK_COMP_SUPABASE_SECRET_KEY") or "").strip()
    legacy_key = (os.getenv("STOCK_COMP_SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    if not supabase_url or not (secret_key or legacy_key):
        raise HTTPException(status_code=503, detail="Trusted plan storage is not configured on the server.")
    if secret_key and not secret_key.startswith("sb_secret_"):
        raise HTTPException(status_code=503, detail=(
            "The server's Supabase secret key has an invalid format. "
            "The server administrator must configure STOCK_COMP_SUPABASE_SECRET_KEY "
            "with a Supabase secret key, or put a legacy service_role key in "
            "STOCK_COMP_SUPABASE_SERVICE_ROLE_KEY instead. Never share keys in chat."
        ))
    if any(character.isspace() for character in (secret_key or legacy_key)):
        raise HTTPException(status_code=503, detail="The server's trusted storage credential contains invalid whitespace.")
    # New sb_secret_* keys are not JWTs: only the apikey header is used.
    # Preserve the existing explicit legacy-key path and Supabase validation.
    headers = {"apikey": secret_key or legacy_key, "Content-Type": "application/json"}
    if not secret_key:
        headers["Authorization"] = f"Bearer {legacy_key}"
    return headers


async def load_account_snapshot(access_token: str, account_id: UUID | None = None) -> dict:
    return await _call_rpc(
        access_token,
        "get_current_account_snapshot",
        {"p_account_id": str(account_id) if account_id else None},
        missing_account_is_not_found=True,
    )


async def create_competition_account(
    access_token: str,
    *,
    name: str,
    starting_capital: float,
    cash: float,
    snapshot_as_of: str,
) -> dict:
    account_id = await _call_rpc(
        access_token,
        "create_or_get_competition_account",
        {
            "p_name": name,
            "p_starting_capital": starting_capital,
            "p_cash": cash,
            "p_snapshot_as_of": snapshot_as_of,
        },
    )
    if not isinstance(account_id, str):
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Account storage returned invalid data.")
    return await load_account_snapshot(access_token, UUID(account_id))


async def update_competition_account_snapshot(
    access_token: str,
    *,
    name: str,
    starting_capital: float,
    cash: float,
    snapshot_as_of: str,
    positions: list[dict],
    expected_portfolio_version: int,
) -> dict:
    account_id = await _call_rpc(
        access_token,
        "update_competition_account_snapshot",
        {
            "p_name": name,
            "p_starting_capital": starting_capital,
            "p_cash": cash,
            "p_snapshot_as_of": snapshot_as_of,
            "p_positions": positions,
            "p_expected_portfolio_version": expected_portfolio_version,
        },
    )
    if not isinstance(account_id, str):
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Account storage returned invalid data.")
    return await load_account_snapshot(access_token, UUID(account_id))


async def save_current_trading_plan(access_token: str, payload: dict, *, user_id: str) -> dict:
    """Persist only an API-generated plan for a verified user, then reload via RLS."""
    if not trusted_plan_storage_configured():
        raise HTTPException(status_code=503, detail="Trusted plan storage is not configured on the server.")
    try:
        verified_user_id = str(UUID(user_id))
    except (TypeError, ValueError, AttributeError) as exc:
        raise HTTPException(status_code=401, detail="Verified user identity is invalid.") from exc
    await _call_trusted_plan_writer(
        {
            "p_user_id": verified_user_id,
            "p_expected_portfolio_version": payload["expected_portfolio_version"],
            "p_strategy_name": payload["strategy_name"],
            "p_market_as_of": payload["market_as_of"],
            "p_summary": payload["summary"],
            "p_portfolio_snapshot": payload["portfolio_snapshot"],
            "p_regime": payload["regime"],
            "p_warnings": payload["warnings"],
            "p_recommendations": payload["recommendations"],
        },
    )
    return await load_account_snapshot(access_token)


async def _call_trusted_plan_writer(body: dict) -> None:
    supabase_url = (os.getenv("STOCK_COMP_SUPABASE_URL") or "").strip().rstrip("/")
    headers = _trusted_plan_writer_headers()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
            response = await client.post(
                f"{supabase_url}/rest/v1/rpc/save_generated_trading_plan",
                headers=headers,
                json=body,
            )
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail="Trusted plan storage timed out.") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Trusted plan storage is unavailable.") from exc
    if response.status_code in {401, 403}:
        raise HTTPException(status_code=503, detail="Trusted plan storage credentials were rejected.")
    if response.status_code == 404:
        raise HTTPException(status_code=503, detail="Trusted plan writer migration is not installed.")
    if response.is_error:
        try:
            code = response.json().get("code")
        except (AttributeError, ValueError):
            code = None
        if code == "40001":
            raise HTTPException(status_code=409, detail="Portfolio changed. Refresh before rebuilding the plan.")
        raise HTTPException(status_code=502, detail="Trusted plan storage returned an error.")
    try:
        UUID(response.json())
    except (TypeError, ValueError, AttributeError) as exc:
        raise HTTPException(status_code=502, detail="Trusted plan storage returned an invalid plan ID.") from exc


async def load_plan_history(access_token: str, limit: int = 20) -> list[dict]:
    result = await _call_rpc(access_token, "get_account_plan_history", {"p_limit": limit})
    if not isinstance(result, list):
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Plan history returned invalid data.")
    if result:
        diagnostics_by_id = await _load_saved_candidate_diagnostics(access_token, result)
        for item in result:
            item["candidate_diagnostics"] = diagnostics_by_id.get(item["id"], {})
    return result


async def _load_saved_candidate_diagnostics(access_token: str, history: list[dict]) -> dict[str, dict]:
    """Read only the saved plan's diagnostic field under table RLS."""
    try:
        ids = [str(UUID(item["id"])) for item in history]
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=502, detail="Plan history returned invalid identities.") from exc
    supabase_url = (os.getenv("STOCK_COMP_SUPABASE_URL") or "").strip().rstrip("/")
    publishable_key = (os.getenv("STOCK_COMP_SUPABASE_PUBLISHABLE_KEY") or "").strip()
    if not supabase_url or not publishable_key:
        raise HTTPException(status_code=503, detail="Authenticated account storage is not configured yet.")
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
            response = await client.get(
                f"{supabase_url}/rest/v1/plans",
                headers={"apikey": publishable_key, "Authorization": f"Bearer {access_token}"},
                params={"select": "id,portfolio_snapshot", "id": f"in.({','.join(ids)})"},
            )
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail="Plan diagnostics timed out.") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Plan diagnostics are unavailable.") from exc
    if response.status_code in {401, 403}:
        raise HTTPException(status_code=403, detail="Plan diagnostics access was denied.")
    if response.is_error:
        raise HTTPException(status_code=502, detail="Plan diagnostics storage returned an error.")
    try:
        rows = response.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="Plan diagnostics storage returned invalid data.") from exc
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise HTTPException(status_code=502, detail="Plan diagnostics storage returned invalid data.")
    known = set(ids)
    diagnostics = {}
    for row in rows:
        if row.get("id") not in known:
            raise HTTPException(status_code=502, detail="Plan diagnostics storage returned an unexpected plan.")
        snapshot = row.get("portfolio_snapshot") or {}
        if not isinstance(snapshot, dict) or not isinstance(snapshot.get("candidate_diagnostics", {}), dict):
            raise HTTPException(status_code=502, detail="Plan diagnostics storage returned invalid data.")
        diagnostics[row["id"]] = snapshot.get("candidate_diagnostics", {})
    if set(diagnostics) != known:
        raise HTTPException(status_code=502, detail="Plan diagnostics are incomplete.")
    return diagnostics


async def change_recommendation_status(
    access_token: str, recommendation_id: UUID, event_type: str,
) -> dict:
    await _call_rpc(
        access_token,
        "set_recommendation_status",
        {"p_recommendation_id": str(recommendation_id), "p_event_type": event_type},
    )
    return await load_account_snapshot(access_token)


async def load_recent_paper_actions(access_token: str, limit: int = 50) -> list[dict]:
    result = await _call_rpc(access_token, "get_recent_paper_actions", {"p_limit": limit}, paper_action=True)
    if not isinstance(result, list):
        raise HTTPException(status_code=502, detail="Paper action history returned invalid data.")
    return result


async def record_recommendation_paper_action(
    access_token: str,
    *,
    recommendation_id: UUID,
    quantity: int,
    expected_portfolio_version: int,
    idempotency_key: str,
) -> dict:
    action_id = await _call_rpc(
        access_token,
        "record_recommendation_paper_action",
        {
            "p_recommendation_id": str(recommendation_id),
            "p_quantity": quantity,
            "p_expected_portfolio_version": expected_portfolio_version,
            "p_idempotency_key": idempotency_key,
        },
        paper_action=True,
    )
    try:
        parsed_id = UUID(action_id)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=502, detail="Paper action storage returned invalid data.") from exc
    return {"action_id": str(parsed_id), "snapshot": await load_account_snapshot(access_token)}


async def list_planning_profiles(access_token: str) -> list[dict]:
    """Return only rows exposed by the caller's JWT and table RLS."""
    result = await _call_profile_table(
        access_token, "GET",
        params={"select": "id,name,mode,rules,created_at,updated_at", "order": "created_at.asc,id.asc"},
    )
    if not isinstance(result, list):
        raise HTTPException(status_code=502, detail="Planning profile storage returned invalid data.")
    return result


async def create_planning_profile(access_token: str, profile: dict) -> dict:
    result = await _call_profile_table(access_token, "POST", payload=profile)
    if not isinstance(result, list) or len(result) != 1:
        raise HTTPException(status_code=502, detail="Planning profile storage returned invalid data.")
    return result[0]


async def update_planning_profile(
    access_token: str, profile_id: UUID, profile: dict, expected_updated_at: str,
) -> dict:
    result = await _call_profile_table(
        access_token, "PATCH", payload=profile,
        params={"id": f"eq.{profile_id}", "updated_at": f"eq.{expected_updated_at}"},
    )
    if result == []:
        raise HTTPException(
            status_code=409,
            detail="This profile changed or is no longer available. Reload before saving.",
        )
    if not isinstance(result, list) or len(result) != 1:
        raise HTTPException(status_code=502, detail="Planning profile storage returned invalid data.")
    return result[0]


async def _call_profile_table(
    access_token: str, method: str, *, payload: dict | None = None,
    params: dict | None = None,
):
    """No service-role key; Postgres RLS owns per-user isolation."""
    supabase_url = (os.getenv("STOCK_COMP_SUPABASE_URL") or "").strip().rstrip("/")
    publishable_key = (os.getenv("STOCK_COMP_SUPABASE_PUBLISHABLE_KEY") or "").strip()
    if not supabase_url or not publishable_key:
        raise HTTPException(status_code=503, detail="Authenticated account storage is not configured yet.")
    headers = {
        "apikey": publishable_key,
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
            response = await client.request(
                method, f"{supabase_url}/rest/v1/planning_profiles",
                headers=headers, params=params, json=payload,
            )
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail="Planning profile storage timed out.") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Planning profile storage is unavailable.") from exc
    if response.status_code in {401, 403}:
        raise HTTPException(status_code=403, detail="Planning profile access was denied.")
    if response.status_code == 404:
        raise HTTPException(
            status_code=503,
            detail="Planning profiles are not installed in cloud storage yet. Apply the planning-profiles migration.",
        )
    if response.is_error:
        raise HTTPException(status_code=502, detail="Planning profile storage returned an error.")
    try:
        return response.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="Planning profile storage returned invalid data.") from exc


async def _call_rpc(
    access_token: str,
    rpc_name: str,
    body: dict,
    *,
    missing_account_is_not_found: bool = False,
    paper_action: bool = False,
):
    supabase_url = (os.getenv("STOCK_COMP_SUPABASE_URL") or "").strip().rstrip("/")
    publishable_key = (os.getenv("STOCK_COMP_SUPABASE_PUBLISHABLE_KEY") or "").strip()
    if not supabase_url or not publishable_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authenticated account storage is not configured yet.",
        )

    headers = {
        "apikey": publishable_key,
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
            response = await client.post(
                f"{supabase_url}/rest/v1/rpc/{rpc_name}",
                headers=headers,
                json=body,
            )
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail="Account storage timed out.") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Account storage is unavailable.") from exc

    if response.status_code in {401, 403}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account access was denied.")
    if response.is_error:
        try:
            provider_payload = response.json()
            provider_code = provider_payload.get("code")
        except (AttributeError, ValueError):
            provider_payload = {}
            provider_code = None
        if paper_action and (response.status_code == 404 or provider_code == "PGRST202"):
            raise HTTPException(status_code=503, detail="Paper action storage is not installed yet. Apply the paper-actions migration.")
        if provider_code == "40001":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Portfolio or plan changed. Refresh and review before saving.",
            )
        if paper_action and provider_code == "P0001":
            allowed_errors = (
                "A recommendation, whole-share quantity", "No competition account exists",
                "Only BUY and review-to-sell", "The stock price is below",
                "Quantity exceeds", "Open SHORT holdings require", "A holding lacks a current plan quote",
                "Insufficient cash", "Shared gross-exposure capacity",
                "The $20,000 per-stock", "There are not enough open LONG shares",
            )
            message = str(provider_payload.get("message") or "")
            detail = message[:200] if message.startswith(allowed_errors) else "Paper action was rejected by account constraints."
            raise HTTPException(status_code=422, detail=detail)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Account storage returned an error.")

    result = response.json()
    if result is None and missing_account_is_not_found:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No competition account exists yet.")
    if result is None:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Account storage returned invalid data.")
    if missing_account_is_not_found and not isinstance(result, dict):
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Account storage returned invalid data.")
    return result
