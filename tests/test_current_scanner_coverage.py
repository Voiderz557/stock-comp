from datetime import date

import pandas as pd

from api.plan_service import plan_payload
from data.current_listing_audit import audit_current_listings
from data.scanner_universe import (
    ADDITIONAL_LIQUID_US_TICKERS,
    BROAD_CURRENT_SCANNER_UNIVERSE,
    EXPANDED_CURRENT_SCANNER_UNIVERSE,
    NASDAQ_100_TEST_UNIVERSE,
    current_scanner_symbols,
)
from data.ticker_history import listed_price_range
from paper_trading.scan_coverage import summarize_scan_coverage


def _frame(price, day):
    return pd.DataFrame({"Close": [price]}, index=pd.DatetimeIndex([day]))


def test_current_scanner_excludes_verified_ended_listings_without_changing_seed():
    assert "EA" in ADDITIONAL_LIQUID_US_TICKERS
    assert "WBD" in NASDAQ_100_TEST_UNIVERSE
    assert "EA" not in EXPANDED_CURRENT_SCANNER_UNIVERSE
    assert "WBD" not in BROAD_CURRENT_SCANNER_UNIVERSE
    assert "MMC" not in BROAD_CURRENT_SCANNER_UNIVERSE
    assert "MRSH" in BROAD_CURRENT_SCANNER_UNIVERSE
    assert "EA" in current_scanner_symbols(["EA", "WBD"], as_of=date(2026, 8, 4))
    assert "WBD" in current_scanner_symbols(["EA", "WBD"], as_of=date(2026, 10, 5))
    assert current_scanner_symbols(["EA", "WBD"], as_of=date(2026, 10, 8)) == []
    assert current_scanner_symbols(["ARM"], as_of=date(2023, 9, 13)) == []
    assert current_scanner_symbols(["ARM"], as_of=date(2023, 9, 14)) == ["ARM"]


def test_exchange_listing_audit_maps_provider_notation_without_autoremoval():
    nasdaq = "Symbol|Test Issue|ETF\nAAPL|N|N\n"
    other = "ACT Symbol|Test Issue|ETF\nBRK.B|N|N\n"
    rows = audit_current_listings(["AAPL", "BRK-B", "MISSING"], nasdaq, other)
    assert [row["status"] for row in rows] == ["LISTED", "LISTED", "UNVERIFIED"]
    assert rows[1]["exchange_symbol"] == "BRK.B"
    assert "do not infer delisting" in rows[2]["reason"]


def test_verified_spcx_listing_boundary_skips_impossible_warmup_requests():
    assert listed_price_range("SPCX", "2025-10-01", "2026-06-12") is None
    assert listed_price_range("SPCX", "2025-10-01", "2026-06-14") == (
        pd.Timestamp("2026-06-12"), pd.Timestamp("2026-06-14")
    )


def test_spcx_prelisting_load_makes_no_provider_request(tmp_path, monkeypatch):
    from data.market_data import load_market_data

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Provider must not be called before the first listing day")

    monkeypatch.setattr("data.market_data._download_with_hard_timeout", forbidden)
    frames, info = load_market_data(
        ["SPCX"], "2025-10-01", "2026-06-11", cache_dir=tmp_path
    )
    assert frames["SPCX"].empty
    assert info["Data Source Failures"] == []


def test_scan_coverage_distinguishes_missing_stale_failed_and_rankable():
    universe = ["GOOD", "STALE", "MISSING", "LOW", "FAILED"]
    frames = {
        "GOOD": _frame(50, "2026-10-08"),
        "STALE": _frame(50, "2026-10-07"),
        "LOW": _frame(3, "2026-10-08"),
        "FAILED": _frame(50, "2026-10-08"),
    }
    plan = {
        "reference_quote_as_of": "2026-10-08T00:00:00Z",
        "candidate_diagnostics": {ticker: {"status": "NOT_SELECTED"} for ticker in universe},
        "cache_info": {"Data Source Failures": [{"Ticker": "FAILED"}]},
    }
    coverage = summarize_scan_coverage(universe, frames, plan, 5)
    assert {key: coverage[key] for key in ("scanned", "quoted", "stale", "missing", "failed", "below_minimum", "eligible")} == {
        "scanned": 5, "quoted": 4, "stale": 1, "missing": 1,
        "failed": 1, "below_minimum": 1, "eligible": 1,
    }
    assert [item["ticker"] for item in coverage["issues"]] == universe[1:]


def test_saved_plan_exposes_coverage_in_summary_history_and_audit():
    coverage = {"scanned": 2, "quoted": 1, "stale": 0, "missing": 1,
                "failed": 1, "eligible": 1, "issues": [
                    {"ticker": "MISS", "reasons": ["No dated positive closing quote"]}
                ]}
    payload = plan_payload(
        {"account": {"portfolio_version": 1}, "portfolio": {"cash": 100_000}, "positions": []},
        {"strategy_name": "Baseline", "market_data_as_of": "2026-10-08",
         "buys": [], "holdings": [], "data_warnings": [], "regime_result": None,
         "universe_mode": "BROAD", "scan_coverage": coverage,
         "scan_stage_timings": {"total_seconds": 2.5}},
    )
    assert "Broad watchlist: 1/2 rankable" in payload["summary"]
    assert payload["portfolio_snapshot"]["scan_coverage"] == coverage
    assert any("Completed in 2.5s" in row for row in payload["warnings"])
    assert any("Scan audit MISS" in row for row in payload["warnings"])
