from datetime import date, datetime, timezone

import pandas as pd

from config import STOCK_UNIVERSE
from data.scanner_universe import NASDAQ_100_TEST_UNIVERSE, BROAD_CURRENT_SCANNER_UNIVERSE, BROAD_RESEARCH_SECTORS, current_scanner_symbols
from market.vix import load_vix_context, summarize_vix


def test_expanded_scanner_preserves_original_watchlist_and_has_no_duplicates():
    assert set(current_scanner_symbols(NASDAQ_100_TEST_UNIVERSE)).issubset(STOCK_UNIVERSE)
    assert {"JPM", "XOM", "JNJ"}.issubset(STOCK_UNIVERSE)
    assert len(STOCK_UNIVERSE) == len(set(STOCK_UNIVERSE))
    assert set(STOCK_UNIVERSE).issubset(BROAD_CURRENT_SCANNER_UNIVERSE)
    assert len(BROAD_CURRENT_SCANNER_UNIVERSE) > len(STOCK_UNIVERSE)
    assert len(BROAD_CURRENT_SCANNER_UNIVERSE) == len(set(BROAD_CURRENT_SCANNER_UNIVERSE))
    assert {"SPY", "QQQ", "^VIX"}.isdisjoint(BROAD_CURRENT_SCANNER_UNIVERSE)
    assert all(group for group in BROAD_RESEARCH_SECTORS.values())


def test_vix_uses_only_dated_close_on_or_before_asof():
    frame = pd.DataFrame({"Close": [18.3, 99.0]}, index=pd.to_datetime(["2026-10-07", "2026-10-09"]))
    result = summarize_vix(frame, as_of=date(2026, 10, 8))
    assert result["value"] == 18.3
    assert result["price_as_of"] == "2026-10-07"
    assert result["status"] == "CURRENT"


def test_vix_missing_or_stale_is_explicit_not_zero():
    assert summarize_vix(None, as_of=date(2026, 10, 8))["value"] is None
    frame = pd.DataFrame({"Close": [17.0]}, index=pd.to_datetime(["2026-09-30"]))
    result = summarize_vix(frame, as_of=date(2026, 10, 8))
    assert result["status"] == "STALE"
    assert result["warning"]


def test_vix_load_failure_never_changes_strategy_or_fabricates_quote():
    def broken_loader(*args):
        raise RuntimeError("Provider unavailable")

    assert load_vix_context(as_of=date(2026, 10, 8), loader=broken_loader)["status"] == "UNAVAILABLE"


def test_live_vix_excludes_unfinished_bar_and_accepts_completed_close():
    frame = pd.DataFrame({"Close": [18.3, 25.0]}, index=pd.to_datetime(["2026-10-29", "2026-10-30"]))
    def loader(*args):
        return {"^VIX": frame}, {}
    morning = load_vix_context(loader=loader, now=datetime(2026, 10, 30, 14, tzinfo=timezone.utc))
    assert morning["price_as_of"] == "2026-10-29"
    assert morning["value"] == 18.3
    evening = load_vix_context(loader=loader, now=datetime(2026, 10, 30, 21, tzinfo=timezone.utc))
    assert evening["price_as_of"] == "2026-10-30"
    assert evening["value"] == 25.0


def test_live_vix_does_not_substitute_incomplete_quote_when_prior_close_missing():
    frame = pd.DataFrame({"Close": [25.0]}, index=pd.to_datetime(["2026-10-30"]))
    result = load_vix_context(loader=lambda *args: ({"^VIX": frame}, {}),
                              now=datetime(2026, 10, 30, 14, tzinfo=timezone.utc))
    assert result["status"] == "UNAVAILABLE"
    assert result["value"] is None
