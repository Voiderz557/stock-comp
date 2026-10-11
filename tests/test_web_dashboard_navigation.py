from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "web" / "app" / "page.tsx"


def test_dashboard_navigation_renders_distinct_views() -> None:
    source = PAGE.read_text(encoding="utf-8")

    for view in ("Overview", "Today's plan", "Portfolio", "History", "Research", "Settings"):
        assert f'active === "{view}"' in source

    assert "How your recommendations changed" in source
    assert "Latest plan versus previous plan" in source
    assert "Current algorithm context" in source
    assert "Data controls" in source


def test_dashboard_controls_have_explicit_behavior() -> None:
    source = PAGE.read_text(encoding="utf-8")

    assert "setNotificationsOpen(true)" in source
    assert "reviewPaperAction(selected)" in source
    assert "changeRecommendationStatus" in source
    assert "factor_details" in source
    assert "Explicit paper action review" in source
    assert "Confirm paper action" in source
    assert "onRecordPaperAction={savePaperAction}" in source
    assert "paperActionsAvailable" in source
    assert 'setActive("Today\'s plan")' in source
    assert 'setActive("Portfolio")' in source
    assert "Add cash & holdings" in source
    assert "Edit account &amp; holdings" in source
    assert "Your daily plan isn’t ready yet." in source
    assert "Build your first daily plan" in source
    assert "Scanning market…" in source
    assert "buildDailyPlan" in source


def test_auth_refresh_updates_api_token_and_signout_clears_snapshot() -> None:
    source = PAGE.read_text(encoding="utf-8")

    assert 'event === "TOKEN_REFRESHED"' in source
    assert 'event === "TOKEN_REFRESHED") {\n        void restoreAccount(session.access_token);' in source
    assert 'setAccessMode("loading")' in source
    assert "clearAccountState();" in source
    assert 'event === "SIGNED_IN"' in source
    assert "void restoreAccount(session.access_token)" in source
    assert "requestVersion++" in source
    assert "generation !== sessionGeneration.current" in source
    assert "return () => { active = false; };" in source
    assert "setSnapshot(DEMO_SNAPSHOT)" in source


def test_signed_out_page_does_not_show_fabricated_market_quotes() -> None:
    source = PAGE.read_text(encoding="utf-8")

    assert "No market data shown before sign-in" in source
    assert "SPY +0.6%" not in source
    assert "QQQ +1.1%" not in source


def test_account_editor_requires_review_and_discloses_snapshot_semantics() -> None:
    source = (ROOT / "web" / "components" / "account-editor.tsx").read_text(encoding="utf-8")

    assert "Review your account snapshot" in source
    assert "not trades" in source
    assert "previous snapshot will remain in history" in source
    assert "appears more than once" in source
    assert "Current prices are not inferred from cost basis" in source
    assert "expected_portfolio_version" in source
    assert "Saving is temporarily unavailable" in source
    assert "1 Enter account" in source
    assert "2 Review" in source
    assert "Nothing will be traded" in source
    assert "Review changes" in source


def test_stale_plan_recommendations_are_hidden_in_web_and_database() -> None:
    page = PAGE.read_text(encoding="utf-8")
    migration = (ROOT / "supabase" / "migrations" / "20260929233000_account_snapshot_updates.sql").read_text(encoding="utf-8")

    assert 'snapshot.plan.status === "CURRENT"' in page
    assert "selected_plan.status <> 'CURRENT'" in migration
    assert "selected_plan.portfolio_version <> selected_account.portfolio_version" in migration


def test_snapshot_replacement_is_versioned_and_preserves_position_rows() -> None:
    migration = (ROOT / "supabase" / "migrations" / "20260929233000_account_snapshot_updates.sql").read_text(encoding="utf-8")

    assert "p_expected_portfolio_version bigint" in migration
    assert "order by portfolio_version desc nulls last, created_at desc" in migration
    assert "set status = 'REPLACED'" in migration
    assert "delete from public.positions" not in migration.lower()
