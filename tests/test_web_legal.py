from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_legal_pages_and_header_policy_exist() -> None:
    for path in (
        ROOT / "web" / "app" / "privacy" / "page.tsx",
        ROOT / "web" / "app" / "cookies" / "page.tsx",
        ROOT / "web" / "app" / "terms" / "page.tsx",
        ROOT / "web" / "public" / "_headers",
    ):
        assert path.is_file(), f"Missing required public-launch file: {path}"


def test_cookie_notice_does_not_claim_nonexistent_tracking() -> None:
    notice = (ROOT / "web" / "app" / "cookies" / "page.tsx").read_text(encoding="utf-8")

    assert "does not use advertising or analytics cookies" in notice
    assert "real consent manager" in notice


def test_public_headers_forbid_framing_and_limit_browser_capabilities() -> None:
    headers = (ROOT / "web" / "public" / "_headers").read_text(encoding="utf-8")

    assert "X-Frame-Options: DENY" in headers
    assert "frame-ancestors 'none'" in headers
    assert "Permissions-Policy: camera=(), microphone=(), geolocation=()" in headers
