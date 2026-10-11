"""Local-development configuration for the public API.

Only browser-safe values from ``web/.env.local`` are imported. Provider
secrets and service-role credentials are deliberately unsupported here.
Production deployments must provide the server environment explicitly.
"""

from __future__ import annotations

import os
from collections.abc import MutableMapping
from pathlib import Path


PUBLIC_TO_SERVER_ENV = {
    "NEXT_PUBLIC_SUPABASE_URL": "STOCK_COMP_SUPABASE_URL",
    "NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY": "STOCK_COMP_SUPABASE_PUBLISHABLE_KEY",
    "NEXT_PUBLIC_DEMO_MODE": "STOCK_COMP_API_DEMO_MODE",
}


def load_local_public_config(
    env_file: Path | None = None,
    environ: MutableMapping[str, str] | None = None,
) -> dict[str, str]:
    """Load browser-safe local settings without overwriting server settings."""

    target_environ = os.environ if environ is None else environ
    path = env_file or Path(__file__).resolve().parents[1] / "web" / ".env.local"
    if not path.is_file():
        return {}

    parsed: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key not in PUBLIC_TO_SERVER_ENV:
            continue
        parsed[key] = value.strip().strip('"').strip("'")

    loaded: dict[str, str] = {}
    for public_name, server_name in PUBLIC_TO_SERVER_ENV.items():
        value = parsed.get(public_name, "")
        if value and not target_environ.get(server_name):
            target_environ[server_name] = value
            loaded[server_name] = value
    return loaded
