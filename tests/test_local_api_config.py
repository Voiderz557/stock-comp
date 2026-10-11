from __future__ import annotations

from pathlib import Path

from api.local_config import load_local_public_config


def test_loads_only_public_local_settings(tmp_path: Path) -> None:
    env_file = tmp_path / ".env.local"
    env_file.write_text(
        "\n".join(
            [
                "NEXT_PUBLIC_SUPABASE_URL=https://example.supabase.co",
                "NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY=public-key",
                "NEXT_PUBLIC_DEMO_MODE=false",
                "GOOGLE_CLIENT_SECRET=must-not-load",
            ]
        ),
        encoding="utf-8",
    )
    environ: dict[str, str] = {}

    loaded = load_local_public_config(env_file, environ)

    assert set(loaded) == {
        "STOCK_COMP_SUPABASE_URL",
        "STOCK_COMP_SUPABASE_PUBLISHABLE_KEY",
        "STOCK_COMP_API_DEMO_MODE",
    }
    assert "GOOGLE_CLIENT_SECRET" not in loaded
    assert "GOOGLE_CLIENT_SECRET" not in environ


def test_explicit_server_settings_are_not_overwritten(tmp_path: Path) -> None:
    env_file = tmp_path / ".env.local"
    env_file.write_text("NEXT_PUBLIC_SUPABASE_URL=https://local.example\n", encoding="utf-8")
    environ = {"STOCK_COMP_SUPABASE_URL": "https://explicit.example"}

    loaded = load_local_public_config(env_file, environ)

    assert "STOCK_COMP_SUPABASE_URL" not in loaded
    assert environ["STOCK_COMP_SUPABASE_URL"] == "https://explicit.example"
