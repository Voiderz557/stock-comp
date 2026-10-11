"""Container startup and liveness probe; does not load local env files."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from urllib.request import ProxyHandler, build_opener


def configured_port() -> int:
    raw = os.environ.get("PORT", "8000")
    try:
        port = int(raw)
    except ValueError as exc:
        raise ValueError("PORT must be an integer between 1 and 65535.") from exc
    if not 1 <= port <= 65535:
        raise ValueError("PORT must be an integer between 1 and 65535.")
    return port


def check_health() -> None:
    # A host's outbound proxy must not intercept the container-local probe.
    opener = build_opener(ProxyHandler({}))
    with opener.open(f"http://127.0.0.1:{configured_port()}/v1/health", timeout=3) as response:
        if response.status != 200 or json.load(response).get("status") != "ok":
            raise RuntimeError("API liveness probe failed.")


def main() -> None:
    if sys.argv[1:] == ["--check-health"]:
        check_health()
        return
    if sys.argv[1:]:
        raise SystemExit("Usage: serve.py [--check-health]")

    import uvicorn

    uvicorn.run(
        "api.public_app:app",
        app_dir=str(Path(__file__).resolve().parents[2]),
        host="0.0.0.0",
        port=configured_port(),
        workers=1,
        proxy_headers=False,
        timeout_graceful_shutdown=170,
    )


if __name__ == "__main__":
    main()
