"""Explicit, self-only cloud account deletion. No local files are touched.

Challenges are short lived, single use, and process local. A restart or another
worker invalidates them safely: the user must review again, never auto-retry.
Auth deletion cascades application rows through the existing foreign keys.
"""
from __future__ import annotations

import hashlib
import os
import secrets
import time
from datetime import datetime, timezone
from uuid import UUID

import httpx
from fastapi import HTTPException

from api.auth import Principal
from api.account_export import export_owned_account_data
from api.cloud_repository import _trusted_plan_writer_headers

CONFIRMATION = "DELETE MY CLOUD ACCOUNT"
CHALLENGE_SECONDS = 300
RECENT_LOGIN_SECONDS = 900
MAX_CHALLENGES = 1000
_challenges: dict[str, tuple[str, str, float]] = {}


def _identity(principal: Principal) -> tuple[str, str, str]:
    if principal.is_demo or not principal.access_token:
        raise HTTPException(403, "Sign in to manage your own cloud account.")
    try:
        user_id = str(UUID(principal.user_id))
    except ValueError:
        raise HTTPException(403, "Account identity is invalid.") from None
    url = os.getenv("STOCK_COMP_SUPABASE_URL", "").rstrip("/")
    key = os.getenv("STOCK_COMP_SUPABASE_PUBLISHABLE_KEY", "")
    if not url.startswith("https://") or not key:
        raise HTTPException(503, "Account deletion is not configured.")
    return user_id, url, key


async def _verify_current_login(client: httpx.AsyncClient, principal: Principal) -> None:
    user_id, url, key = _identity(principal)
    response = await client.get(f"{url}/auth/v1/user", headers={
        "apikey": key, "Authorization": f"Bearer {principal.access_token}"})
    if response.status_code != 200:
        raise HTTPException(401, "Sign in again before reviewing account deletion.")
    try:
        user = response.json()
        signed_in = datetime.fromisoformat(user["last_sign_in_at"].replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - signed_in).total_seconds()
        valid = user["id"] == user_id and 0 <= age <= RECENT_LOGIN_SECONDS
    except (ValueError, TypeError, KeyError, AttributeError):
        valid = False
    if not valid:
        raise HTTPException(401, "Sign out and sign in again; deletion requires a sign-in within 15 minutes.")


async def preview_deletion(principal: Principal) -> dict:
    user_id, _, _ = _identity(principal)
    _trusted_plan_writer_headers()  # Fail before presenting an unusable review.
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
            await _verify_current_login(client, principal)
        archive = await export_owned_account_data(principal.access_token)
    except httpx.HTTPError:
        raise HTTPException(503, "Account deletion review is unavailable. Nothing was deleted.") from None
    now = time.monotonic()
    for token, (owner, _, expiry) in list(_challenges.items()):
        if expiry <= now or owner == user_id:
            _challenges.pop(token, None)
    if len(_challenges) >= MAX_CHALLENGES:
        raise HTTPException(503, "Account deletion review is busy. Try again later.")
    challenge = secrets.token_urlsafe(32)
    digest = hashlib.sha256(principal.access_token.encode()).hexdigest()
    _challenges[challenge] = (user_id, digest, now + CHALLENGE_SECONDS)
    return {"challenge": challenge, "expires_in_seconds": CHALLENGE_SECONDS,
            "reviewed_at": archive["exported_at"], "counts": archive["counts"],
            "confirmation": CONFIRMATION,
            "scope": "All cloud accounts, holdings, plans, actions, settings and app sign-in belonging to you. Counts are a sequential preview; changes made afterward are also removed.",
            "excluded": "Local desktop databases, downloaded exports, Google/Microsoft accounts and provider backups are not removed by this action."}


async def confirm_deletion(principal: Principal, challenge: str, confirmation: str, acknowledged: bool) -> dict:
    user_id, url, _ = _identity(principal)
    if confirmation != CONFIRMATION or acknowledged is not True:
        raise HTTPException(422, "Type the exact confirmation and acknowledge permanent deletion.")
    digest = hashlib.sha256(principal.access_token.encode()).hexdigest()
    entry = _challenges.get(challenge)
    if not entry or entry[:2] != (user_id, digest) or entry[2] <= time.monotonic():
        raise HTTPException(409, "Deletion review expired or the session changed. Review again.")
    # Consume before the first await: duplicate clicks cannot issue two deletes.
    _challenges.pop(challenge)
    headers = _trusted_plan_writer_headers()
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
            await _verify_current_login(client, principal)
            response = await client.request("DELETE", f"{url}/auth/v1/admin/users/{user_id}",
                                            headers=headers, json={"should_soft_delete": False})
    except httpx.HTTPError:
        raise HTTPException(503, "Deletion outcome could not be confirmed. Do not retry automatically; sign out and check your account before requesting another review.") from None
    if response.status_code not in (200, 204):
        raise HTTPException(503, "The provider did not confirm account deletion. No success is claimed. Contact support if this persists.")
    return {"deleted": True, "scope": "Your cloud app account and linked application records.",
            "message": "Sign out on all devices. Local files and provider backups are outside this deletion."}
