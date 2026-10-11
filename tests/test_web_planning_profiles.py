"""Static checks for the single competition workspace and optional guidance."""

from pathlib import Path


ROOT = Path(__file__).parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_main_workspace_has_settings_instead_of_profile_switcher():
    source = read("web/app/page.tsx")
    assert "return <TradingViewWorkspace" not in source
    assert "<ProfileControls" not in source
    assert "Recommendation settings" in source
    assert "Show automatic stop-loss SELL guidance" in source
    assert "Show automatic take-profit SELL guidance" in source
    assert "Any eligible price (algorithm decides)" in source
    assert "No price minimum — research only" in source
    assert 'selected.price < 5' in source
    assert 'paperAction.price < 5' in source
    assert "VIX · implied volatility" in source
    assert "Broad cross-sector watchlist" in source


def test_old_profile_data_remains_available_but_not_visible_as_workspaces():
    source = read("web/app/page.tsx")
    assert "loadPlanningProfiles" in source
    assert "savePlanningProfile" in source
    assert 'name: "App settings"' in source
    assert "savedAppSettingsProfile(planningProfiles)" in source
    assert "managedAppSettingsProfile(planningProfiles)" in source
    assert 'profile.name === "App settings"' in read("web/lib/planning-settings.ts")
    assert "stock-comp-selected-planning-profile" not in source


def test_exit_guidance_is_opt_in_and_no_order_is_placed():
    source = read("web/app/page.tsx")
    assert 'selected.action === "BUY" && (stopLossEnabled || takeProfitEnabled)' in source
    assert "not submitted orders" in source
    assert "onStopPreview" in source
