"""Guard the optional direct Google path and honest provider availability UI."""

from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_google_sign_in_uses_nonce_and_keeps_redirect_fallback():
    source = (ROOT / "web" / "components" / "google-sign-in.tsx").read_text(encoding="utf-8")
    page = (ROOT / "web" / "app" / "page.tsx").read_text(encoding="utf-8")
    assert "crypto.getRandomValues" in source
    assert 'crypto.subtle.digest("SHA-256"' in source
    assert "nonce: nonce.hashed" in source
    assert "nonce: nonce.raw" in source
    assert "signInWithIdToken" in source
    assert "console.log" not in source
    assert "Use Google redirect instead" in page
    assert 'continueWith("google")' in page


def test_microsoft_button_tracks_remote_provider_configuration():
    source = (ROOT / "web" / "lib" / "supabase.ts").read_text(encoding="utf-8")
    page = (ROOT / "web" / "app" / "page.tsx").read_text(encoding="utf-8")
    assert "settings.external?.azure === true" in source
    assert 'scopes: provider === "azure" ? "email"' in source
    assert "{microsoftReady && <button" in page
    assert "Microsoft sign-in is not configured yet" in page
