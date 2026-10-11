"""Supabase JWT validation for the public API.

The desktop and web clients are public clients. They may provide a Supabase
access token, but no service-role or provider secret belongs in either client.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache

import jwt
from fastapi import Header, HTTPException, status
from jwt import PyJWKClient


@dataclass(frozen=True)
class Principal:
    user_id: str
    email: str | None = None
    is_demo: bool = False
    access_token: str | None = field(default=None, repr=False, compare=False)


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


@lru_cache(maxsize=4)
def _jwk_client(supabase_url: str) -> PyJWKClient:
    return PyJWKClient(f"{supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json")


def clear_jwk_cache() -> None:
    """Test/reconfiguration helper; does not expose or retain credentials."""

    _jwk_client.cache_clear()


def require_principal(authorization: str | None = Header(default=None)) -> Principal:
    """Return a verified principal or fail closed.

    Demo access is explicit and local-development-only. When demo mode is off,
    missing configuration, missing tokens, and invalid tokens never fall back
    to an anonymous or shared user.
    """

    demo_mode = _truthy(os.getenv("STOCK_COMP_API_DEMO_MODE"))
    if demo_mode and not authorization:
        return Principal(user_id="demo-user", email=None, is_demo=True)

    supabase_url = (os.getenv("STOCK_COMP_SUPABASE_URL") or "").strip()
    if not supabase_url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication is not configured.",
        )

    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="A valid bearer token is required.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    issuer = f"{supabase_url.rstrip('/')}/auth/v1"
    audience = (os.getenv("STOCK_COMP_SUPABASE_JWT_AUDIENCE") or "authenticated").strip()
    try:
        signing_key = _jwk_client(supabase_url).get_signing_key_from_jwt(token)
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256", "ES256"],
            audience=audience,
            issuer=issuer,
            options={"require": ["exp", "iat", "sub"]},
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="The access token is invalid or expired.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    user_id = payload.get("sub")
    if not isinstance(user_id, str) or not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token subject is missing.")

    email = payload.get("email")
    return Principal(
        user_id=user_id,
        email=email if isinstance(email, str) else None,
        access_token=token,
    )
