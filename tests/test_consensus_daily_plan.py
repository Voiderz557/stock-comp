"""Advisory sizing and cross-strategy consensus regression tests."""

from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
import pytest

from config import MAX_POSITION_VALUE
from api.plan_service import plan_payload
from market.regime import DOWNTREND, STRONG_UPTREND
from paper_trading.trading_plan import (
    CONSENSUS_MODE,
    analyze_ticker,
    allocate_buy_candidates,
    build_trading_plan,
    load_and_build_trading_plan,
    plan_mode_options,
    _plan_data_warnings,
)
from strategies.registry import get_strategy, live_strategy_names


NAMES = live_strategy_names()


def _prices(tickers):
    return {
        ticker: pd.DataFrame(
            {"Close": [100.0], "Open": [100.0], "Volume": [1000]},
            index=pd.DatetimeIndex(["2026-10-06"]),
        )
        for ticker in tickers
    }


def _getter(signals, scales=None):
    scales = scales or {}

    def get(name):
        def analyze(ticker, data, benchmark_data=None):
            return {
                "Ticker": ticker, "Signal": signals[name][ticker],
                "Score": scales.get(name, 1) * (100 - ord(ticker[0])),
                "Reason": f"{name}: {signals[name][ticker]}",
            }

        return SimpleNamespace(
            name=name, analyze=analyze, rank_key=lambda result: result["Score"],
            required_history_days=200, benchmark_ticker="QQQ" if "Relative Strength" in name else None,
        )

    return get


def _account():
    return {"Cash": 100_000.0, "Starting Capital": 100_000.0}


def _regime(name=STRONG_UPTREND):
    return SimpleNamespace(regime=name)


def test_consensus_is_selectable_and_every_live_strategy_is_called():
    assert CONSENSUS_MODE in plan_mode_options()
    assert "Controlled Breakout V1" not in plan_mode_options()
    assert get_strategy("Controlled Breakout V1").live_enabled is False
    tickers = list("ABCDEFG")
    calls = []
    getter = _getter({name: {ticker: "BUY" for ticker in tickers} for name in NAMES})

    def tracked(name):
        calls.append(name)
        return getter(name)

    plan = build_trading_plan(
        CONSENSUS_MODE, _account(), [], _prices(tickers), tickers,
        regime_result=_regime(), get_strategy_fn=tracked,
        target_position_value=10_000, whole_shares=True,
    )
    assert set(calls) == set(NAMES)
    assert len(plan["buys"]) == 7  # More than five when qualifying candidates exist.
    assert all(row["Quantity"] == 100 for row in plan["buys"])
    assert plan["cash_after"] == pytest.approx(30_000)
    assert plan["buys"][0]["Factor Details"]["Strategy Signals"] == {name: "BUY" for name in NAMES}


def test_consensus_does_not_add_incompatible_raw_strategy_scores():
    tickers = ["AAA", "BBB"]
    signals = {name: {ticker: "BUY" for ticker in tickers} for name in NAMES}
    base = build_trading_plan(
        CONSENSUS_MODE, _account(), [], _prices(tickers), tickers,
        regime_result=_regime(), get_strategy_fn=_getter(signals),
    )
    scaled = build_trading_plan(
        CONSENSUS_MODE, _account(), [], _prices(tickers), tickers,
        regime_result=_regime(), get_strategy_fn=_getter(signals, {NAMES[0]: 1_000_000}),
    )
    assert [(row["Ticker"], row["Score"]) for row in base["buys"]] == [
        (row["Ticker"], row["Score"]) for row in scaled["buys"]
    ]


def test_consensus_leaves_cash_in_downtrend_and_excludes_missing_strategy_output():
    tickers = ["AAA", "BBB"]
    signals = {name: {ticker: "BUY" for ticker in tickers} for name in NAMES}
    bear = build_trading_plan(
        CONSENSUS_MODE, _account(), [], _prices(tickers), tickers,
        regime_result=_regime(DOWNTREND), get_strategy_fn=_getter(signals),
    )
    assert bear["buys"] == []
    assert bear["cash_after"] == 100_000
    del signals[NAMES[0]]["AAA"]
    partial = build_trading_plan(
        CONSENSUS_MODE, _account(), [], _prices(tickers), tickers,
        regime_result=_regime(), get_strategy_fn=_getter(signals),
    )
    assert "AAA" in partial["unavailable_universe"]
    assert all(row["Ticker"] != "AAA" for row in partial["buys"])
    assert partial["candidate_diagnostics"]["AAA"]["status"] == "UNAVAILABLE"


def test_candidate_diagnostics_explain_signal_and_allocation_without_changing_buys():
    tickers = ["AAA", "BBB"]
    signals = {name: {"AAA": "BUY", "BBB": "WAIT"} for name in NAMES}
    plan = build_trading_plan(
        CONSENSUS_MODE, {"Cash": 0.0, "Starting Capital": 100_000.0}, [],
        _prices(tickers), tickers, regime_result=_regime(),
        get_strategy_fn=_getter(signals), whole_shares=True,
    )
    assert plan["buys"] == []
    assert plan["candidate_diagnostics"]["AAA"]["status"] == "ALLOCATION_BLOCKED"
    assert "cash" in plan["candidate_diagnostics"]["AAA"]["reason"].lower()
    assert plan["candidate_diagnostics"]["BBB"]["status"] == "NOT_SELECTED"
    assert "BUY votes" in plan["candidate_diagnostics"]["BBB"]["reason"]


def test_selected_strategy_records_wait_and_avoid_without_changing_signals():
    signals = {name: {"AAA": "WAIT", "BBB": "AVOID"} for name in NAMES}
    plan = build_trading_plan(
        NAMES[0], _account(), [], _prices(["AAA", "BBB"]), ["AAA", "BBB"],
        get_strategy_fn=_getter(signals),
    )
    assert plan["buys"] == []
    assert plan["candidate_diagnostics"]["AAA"]["signal"] == "WAIT"
    assert plan["candidate_diagnostics"]["BBB"]["signal"] == "AVOID"
    assert plan["candidate_diagnostics"]["BBB"]["status"] == "NOT_SELECTED"


def test_consensus_holding_review_uses_all_signals_without_executing_exit():
    signals = {name: {"AAA": "WAIT"} for name in NAMES}
    for name in NAMES[:2]:
        signals[name]["AAA"] = "AVOID"
    position = {
        "ticker": "AAA", "direction": "LONG", "quantity": 10,
        "entry_price": 90, "allocated_capital": 900,
    }
    plan = build_trading_plan(
        CONSENSUS_MODE, _account(), [position], _prices(["AAA"]), ["AAA"],
        regime_result=_regime(), get_strategy_fn=_getter(signals),
    )
    review = plan["holdings"][0]
    assert review["Action"] == "REVIEW EXIT"
    assert review["Score"] == pytest.approx(100 * (len(NAMES) - 2) * 0.5 / len(NAMES))
    assert len(review["Factor Details"]["Strategy Signals"]) == len(NAMES)
    assert plan["cash_before"] == plan["cash_after"]


def test_consensus_does_not_buy_more_of_holding_flagged_for_exit():
    signals = {name: {"AAA": "BUY"} for name in NAMES}
    for name in NAMES[:2]:
        signals[name]["AAA"] = "AVOID"
    position = {
        "ticker": "AAA", "direction": "LONG", "quantity": 10,
        "entry_price": 90, "allocated_capital": 900,
    }
    plan = build_trading_plan(
        CONSENSUS_MODE, _account(), [position], _prices(["AAA"]), ["AAA"],
        regime_result=_regime(), get_strategy_fn=_getter(signals),
    )
    assert plan["holdings"][0]["Action"] == "REVIEW EXIT"
    assert plan["buys"] == []
    assert "review exit before adding" in plan["candidate_diagnostics"]["AAA"]["reason"].lower()


def test_research_only_strategy_cannot_be_selected_for_live_plan():
    with pytest.raises(ValueError, match="not enabled for live paper planning"):
        build_trading_plan(
            "Controlled Breakout V1", _account(), [], _prices(["AAA"]), ["AAA"],
        )


def test_whole_share_target_respects_cash_capacity_and_long_cap():
    quotes = {"AAA": {"price": 333.33, "as_of": "2026-10-06"}, "BBB": {"price": 100, "as_of": "2026-10-06"}}
    ranked = [{"Ticker": ticker, "Score": 1} for ticker in quotes]
    result = allocate_buy_candidates(
        ranked, _account(), [], quotes, target_position_value=10_000, whole_shares=True
    )
    assert result["buys"][0]["Quantity"] == 30
    assert result["buys"][0]["Allocation"] == pytest.approx(9999.9)
    assert all(row["Allocation"] <= MAX_POSITION_VALUE for row in result["buys"])
    assert result["cash_after"] == pytest.approx(100_000 - sum(row["Allocation"] for row in result["buys"]))
    with pytest.raises(ValueError, match="Per-stock target"):
        allocate_buy_candidates(ranked, _account(), [], quotes, target_position_value=20_001)


def test_stale_stock_quote_cannot_become_buy_allocation():
    quotes = {
        "AAA": {"price": 100.0, "as_of": "2026-10-05"},
        "BBB": {"price": 100.0, "as_of": "2026-10-06"},
    }
    result = allocate_buy_candidates(
        [{"Ticker": "AAA", "Score": 90}, {"Ticker": "BBB", "Score": 80}],
        _account(), [], quotes, whole_shares=True, reference_quote_as_of="2026-10-06",
    )
    assert [row["Ticker"] for row in result["buys"]] == ["BBB"]
    assert "before the market reference session" in result["skipped"][0]["Reason"]
    assert result["cash_after"] == 80_000


def test_plan_rejects_stale_buy_and_warns_on_stale_holding_quote():
    signals = {name: {"AAA": "BUY"} for name in NAMES}
    prices = _prices(["AAA"])
    prices["SPY"] = pd.DataFrame(
        {"Close": [100.0], "Open": [100.0], "Volume": [1000]},
        index=pd.DatetimeIndex(["2026-10-07"]),
    )
    position = {"ticker": "AAA", "direction": "LONG", "quantity": 1, "entry_price": 90, "allocated_capital": 90}
    plan = build_trading_plan(
        NAMES[0], _account(), [position], prices, ["AAA"],
        get_strategy_fn=_getter(signals),
    )
    assert plan["buys"] == []
    assert plan["candidate_diagnostics"]["AAA"]["status"] == "ALLOCATION_BLOCKED"
    assert "existing holdings lack a quote" in plan["no_purchase_explanation"]
    assert any("holding quote is dated" in warning for warning in _plan_data_warnings(plan))


def test_consensus_loads_max_strategy_warmup_and_regime_once():
    tickers = ["AAA"]
    signals = {name: {"AAA": "BUY"} for name in NAMES}
    getter = _getter(signals)
    requests = []
    statuses = []

    def load(tickers, start, end, status_callback=None):
        requests.append((tuple(tickers), pd.Timestamp(start), pd.Timestamp(end)))
        return _prices(set(tickers)), {}

    with patch("paper_trading.trading_plan.compute_market_features", return_value=object()), patch(
        "paper_trading.trading_plan.classify_market_regime", return_value=_regime()
    ):
        plan = load_and_build_trading_plan(
            CONSENSUS_MODE, _account(), [], universe=tickers,
            load_market_data_fn=load, as_of_date="2026-10-06", get_strategy_fn=getter,
            target_position_value=10_000, whole_shares=True,
            status_callback=lambda stage, detail: statuses.append((stage, detail)),
        )
    assert len(requests) == 2  # One regime request, one common feature-history request.
    assert set(requests[1][0]) >= {"AAA", "SPY", "QQQ"}
    assert (requests[1][2] - requests[1][1]).days >= 200
    assert plan["buys"][0]["Quantity"] == 100
    assert plan["scan_coverage"]["scanned"] == 1
    assert plan["scan_coverage"]["eligible"] == 1
    assert plan["scan_stage_timings"]["total_seconds"] >= 0
    assert statuses[-1][0] == "Scan complete"


def test_default_live_plan_loader_uses_bounded_workers_without_changing_injected_loader():
    signals = {name: {"AAA": "BUY"} for name in NAMES}
    worker_counts = []

    def fake_loader(tickers, start, end, status_callback=None, max_workers=1, live_failure_retry=False):
        worker_counts.append((max_workers, live_failure_retry))
        return _prices(set(tickers)), {}

    with patch("data.market_data.load_market_data", side_effect=fake_loader), patch(
        "paper_trading.trading_plan.compute_market_features", return_value=object()
    ), patch("paper_trading.trading_plan.classify_market_regime", return_value=_regime()):
        plan = load_and_build_trading_plan(
            CONSENSUS_MODE, _account(), [], universe=["AAA"],
            as_of_date="2026-10-06", get_strategy_fn=_getter(signals),
        )
    assert worker_counts == [(4, True), (4, True)]
    assert plan["buys"]


def test_live_plan_excludes_unfinished_same_day_bar_from_signals_and_quote():
    from datetime import datetime, timezone

    captured = {}
    frame = pd.DataFrame(
        {"Open": [100.0, 999.0], "Close": [100.0, 999.0], "Volume": [1000, 1000]},
        index=pd.DatetimeIndex(["2026-10-07", "2026-10-08"]),
    )

    def load(tickers, start, end, status_callback=None):
        return {ticker: frame.copy() for ticker in tickers}, {}

    def build(*args, **kwargs):
        captured["price_data"] = args[3]
        return {"buys": [], "holdings": [], "unavailable_universe": []}

    with patch("paper_trading.trading_plan.datetime", SimpleNamespace(
        now=lambda tz: datetime(2026, 10, 8, 18, 0, tzinfo=timezone.utc)
    )), patch("paper_trading.trading_plan.build_trading_plan", side_effect=build), patch(
        "paper_trading.trading_plan.summarize_scan_coverage",
        return_value={"eligible": 0, "scanned": 1},
    ):
        plan = load_and_build_trading_plan(
            "Baseline", _account(), [], universe=["AAA"], load_market_data_fn=load,
        )

    assert captured["price_data"]["AAA"].index[-1] == pd.Timestamp("2026-10-07")
    assert plan["market_data_as_of"].startswith("2026-10-07")


def test_volume_gap_cannot_attach_current_quote_to_older_breakout_signal():
    definition = get_strategy("Breakout Volume V1")
    dates = pd.bdate_range("2025-01-02", periods=220)
    closes = [100.0 + index * 0.5 for index in range(len(dates))]
    frame = pd.DataFrame({
        "Open": closes, "Close": closes, "Volume": [1000.0] * len(dates),
    }, index=dates)
    assert analyze_ticker(definition, "AAA", {"AAA": frame}) is not None
    frame.iloc[-1, frame.columns.get_loc("Volume")] = float("nan")
    stale_result = definition.analyze("AAA", frame)
    assert stale_result["Factor Details"]["Price"] != frame["Close"].iloc[-1]
    assert analyze_ticker(definition, "AAA", {"AAA": frame}) is None


def test_cloud_payload_preserves_estimated_shares_and_strategy_evidence():
    plan = {
        "strategy_name": CONSENSUS_MODE,
        "market_data_as_of": "2026-10-06",
        "buys": [{
            "Ticker": "AAA", "Signal": "BUY", "Score": 72.5,
            "Reason": "Several strategies agree.", "Price": 100.0,
            "Price As Of": "2026-10-06", "Quantity": 100,
            "Allocation": 10_000.0,
            "Factor Details": {"Strategy Signals": {name: "BUY" for name in NAMES}},
        }],
        "holdings": [], "data_warnings": [], "regime_result": None,
        "candidate_diagnostics": {"AAA": {"status": "RECOMMENDED", "reason": "Qualified."}},
    }
    payload = plan_payload(
        {"account": {"portfolio_version": 1}, "portfolio": {"cash": 100_000}, "positions": []},
        plan,
    )
    details = payload["recommendations"][0]["factor_details"]
    assert details["Proposed Quantity"] == 100
    assert details["Proposed Allocation"] == 10_000
    assert details["Strategy Signals"] == {name: "BUY" for name in NAMES}
    assert payload["portfolio_snapshot"]["candidate_diagnostics"]["AAA"]["status"] == "RECOMMENDED"
