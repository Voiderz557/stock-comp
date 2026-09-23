"""Environment-only AI settings. Keys are never stored in source or snapshots."""

from __future__ import annotations

import os


ENV_ENABLED = "STOCK_COMP_AI_ENABLED"
ENV_PROVIDER = "STOCK_COMP_AI_PROVIDER"
ENV_MODEL = "STOCK_COMP_AI_MODEL"
ENV_API_KEY = "STOCK_COMP_AI_API_KEY"
ENV_BASE_URL = "STOCK_COMP_AI_BASE_URL"
OPENAI_KEY_FALLBACK = "OPENAI_API_KEY"

DEFAULT_PROVIDER = "none"
DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_BASE_URL = "https://api.openai.com/v1"
REQUEST_TIMEOUT_SECONDS = 30
MAX_QUESTION_CHARS = 2_000
MAX_RESPONSE_CHARS = 8_000
MAX_OUTPUT_TOKENS = 1_200


def ai_ui_enabled():
    """AI stays hidden unless explicitly turned on."""
    return os.environ.get(ENV_ENABLED, "").strip().lower() in {"1", "true", "yes", "on"}


def configured_provider_name():
    return (os.environ.get(ENV_PROVIDER) or DEFAULT_PROVIDER).strip().lower()


def configured_model():
    return (os.environ.get(ENV_MODEL) or DEFAULT_MODEL).strip()


def configured_base_url():
    return (os.environ.get(ENV_BASE_URL) or DEFAULT_BASE_URL).rstrip("/")


def api_key():
    return os.environ.get(ENV_API_KEY) or os.environ.get(OPENAI_KEY_FALLBACK) or ""


def public_provider_label():
    name = configured_provider_name()
    if name in {"openai", "openai_compatible"}:
        return "openai_compatible"
    if name in {"none", "", "off"}:
        return "none"
    return name


def public_config():
    """Safe to show in the UI. Never includes the API key."""
    return {
        "provider": public_provider_label(),
        "model": configured_model(),
        "configured": bool(api_key()) and public_provider_label() != "none",
        "enabled": ai_ui_enabled(),
    }
