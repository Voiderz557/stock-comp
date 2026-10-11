"""Small, auditable security controls for the public API.

The process-local limiter is a defensive backstop. A public deployment should
also enforce distributed limits at its reverse proxy or hosting edge.
"""

from __future__ import annotations

import os
import threading
import time
from collections import defaultdict, deque

from fastapi import Request
from fastapi.responses import JSONResponse, Response


SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}


def positive_int_env(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a positive integer.") from exc
    if value <= 0:
        raise RuntimeError(f"{name} must be a positive integer.")
    return value


class FixedWindowRateLimiter:
    """Thread-safe per-process limiter keyed by client and request class."""

    def __init__(self, window_seconds: int = 60) -> None:
        self.window_seconds = window_seconds
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str, limit: int, now: float | None = None) -> tuple[bool, int, int]:
        timestamp = time.monotonic() if now is None else now
        cutoff = timestamp - self.window_seconds
        with self._lock:
            events = self._events[key]
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= limit:
                retry_after = max(1, int(self.window_seconds - (timestamp - events[0])) + 1)
                return False, 0, retry_after
            events.append(timestamp)
            return True, max(0, limit - len(events)), 0


def client_rate_key(request: Request, request_class: str) -> str:
    # Do not trust X-Forwarded-For unless a deployment explicitly configures a
    # trusted proxy. Uvicorn supplies the direct peer here.
    host = request.client.host if request.client else "unknown"
    return f"{host}:{request_class}"


def secure_response(response: Response) -> Response:
    for name, value in SECURITY_HEADERS.items():
        response.headers[name] = value
    if (os.getenv("STOCK_COMP_HTTPS_ONLY") or "").strip().lower() in {"1", "true", "yes", "on"}:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


def rate_limited_response(limit: int, retry_after: int) -> Response:
    response = JSONResponse(status_code=429, content={"detail": "Too many requests. Try again later."})
    response.headers["Retry-After"] = str(retry_after)
    response.headers["X-RateLimit-Limit"] = str(limit)
    response.headers["X-RateLimit-Remaining"] = "0"
    return secure_response(response)
