"""No accounts, network, cache, or runtime portfolio are touched by these tests."""

from copy import deepcopy
from datetime import datetime, timezone
import json

import pandas as pd
import pytest

from api import recommendation_journal as journal


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    monkeypatch.setattr(journal, "_now", lambda: datetime(2026, 10, 23, 22, tzinfo=timezone.utc))


def history(**overrides):
    return [{
        "id": "saved-plan", "generated_at": "2026-01-05T22:00:00Z",
        "market_as_of": "2026-01-05T00:00:00Z", "strategy": "original",
        "status": "REPLACED", "recommendations": [
            {"ticker": "AAA", "action": "BUY", "score": 12, "rank": 1,
             "reason": "original reason", "status": "OPEN"},
        ], **overrides,
    }]


def prices():
    # Independent expected calendar, not the implementation's calendar helper.
    dates = pd.bdate_range("2026-01-05", "2026-05-01")
    dates = dates.difference(pd.to_datetime(["2026-01-19", "2026-02-16", "2026-04-03"]))
    return {"AAA": pd.DataFrame({"Close": [100.0 + n for n in range(len(dates))]}, index=dates),
            "SPY": pd.DataFrame({"Close": [400.0 + n for n in range(len(dates))]}, index=dates)}


def first(result, horizon=5):
    return next(outcome for outcome in result["entries"][0]["outcomes"] if outcome["sessions"] == horizon)


def test_source_immutability_and_original_record_survives_output_edits():
    rows, frames = history(), prices()
    original, original_frame = deepcopy(rows), frames["AAA"].copy(deep=True)
    result = journal.build_recommendation_journal(rows, frames, "2026-05-01")
    assert result["source_history"] == original
    result["entries"][0]["recommendation"]["score"] = 999
    result["source_history"][0]["recommendations"].clear()
    assert rows == original
    pd.testing.assert_frame_equal(frames["AAA"], original_frame)


@pytest.mark.parametrize("horizon,end", [(5, "2026-01-13"), (20, "2026-02-04"), (60, "2026-04-02")])
def test_exact_dates_and_maturity(horizon, end):
    frames = prices()
    before = (pd.Timestamp(end) - pd.Timedelta(days=1)).date()
    immature = first(journal.build_recommendation_journal(history(), frames, before), horizon)
    assert immature["status"] == "maturing"
    assert immature["null_reason"] == "maturing"
    assert immature["stock_return"] is None
    mature = journal.build_recommendation_journal(history(), frames, end)
    assert mature["entries"][0]["reference_date"] == "2026-01-06"
    outcome = first(mature, horizon)
    assert outcome["end_date"] == end
    assert outcome["status"] == "complete"
    assert outcome["stock_return"] == pytest.approx(frames["AAA"].loc[end, "Close"] / 101 - 1)
    assert outcome["spy_return"] == pytest.approx(frames["SPY"].loc[end, "Close"] / 401 - 1)
    assert outcome["excess_return"] == pytest.approx(outcome["stock_return"] - outcome["spy_return"])


def test_generation_after_signal_delays_reference_and_uses_new_york_date():
    rows = history(generated_at="2026-01-08T02:00:00Z")  # Jan 7 in New York
    result = journal.build_recommendation_journal(rows, prices(), "2026-05-01")
    assert result["entries"][0]["reference_date"] == "2026-01-08"
    # Intraday publication also deliberately waits for the next session.
    rows[0]["generated_at"] = "2026-01-08T15:00:00Z"
    assert journal.build_recommendation_journal(rows)["entries"][0]["reference_date"] == "2026-01-09"


def test_holiday_and_weekend_do_not_count_as_sessions():
    result = journal.build_recommendation_journal(
        history(generated_at="2026-07-02T22:00:00Z", market_as_of="2026-07-02"))
    assert result["entries"][0]["reference_date"] == "2026-07-06"
    assert first(result)["end_date"] == "2026-07-13"


def test_exceptional_mourning_closure_is_not_a_session():
    result = journal.build_recommendation_journal(
        history(generated_at="2025-01-08T22:00:00Z", market_as_of="2025-01-08"))
    assert result["entries"][0]["reference_date"] == "2025-01-10"


@pytest.mark.parametrize("symbol", ["AAA", "SPY"])
@pytest.mark.parametrize("missing", ["2026-01-06", "2026-01-08", "2026-01-13"])
def test_missing_prices_never_shift_reference_horizon_or_spy_alignment(symbol, missing):
    frames = prices()
    frames[symbol] = frames[symbol].drop(pd.Timestamp(missing))
    result = journal.build_recommendation_journal(history(), frames, "2026-05-01")
    outcome = first(result)
    key = "stock" if symbol == "AAA" else "spy"
    assert outcome[f"{key}_return"] is None
    assert outcome[f"{key}_null_reason"] == f"missing_session:{missing}"
    assert outcome["end_date"] == "2026-01-13"
    assert result["entries"][0]["reference_date"] == "2026-01-06"
    assert outcome["excess_return"] is None


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), 0, -1, "bad"])
def test_invalid_close_is_not_filled(bad):
    frames = prices()
    frames["AAA"] = frames["AAA"].astype(object)
    frames["AAA"].loc["2026-01-08", "Close"] = bad
    outcome = first(journal.build_recommendation_journal(history(), frames, "2026-05-01"))
    assert outcome["stock_return"] is None
    assert outcome["stock_null_reason"] == "invalid_close:2026-01-08"


def test_duplicate_session_rejected_and_json_has_no_nan():
    frames = prices()
    frames["AAA"] = pd.concat([frames["AAA"], frames["AAA"].iloc[:1]])
    result = journal.build_recommendation_journal(history(), frames, "2026-05-01")
    assert first(result)["stock_null_reason"] == "duplicate_session"
    json.dumps(result, allow_nan=False)


def test_all_statuses_actions_and_deleted_symbols_remain_no_performance_claim():
    rows = history()
    rows[0]["recommendations"] = [
        {"ticker": ticker, "status": status, "action": action}
        for ticker, status, action in [("AAA", "OPEN", "BUY"), ("AAA", "DISMISSED", "AVOID"),
                                      ("AAA", "RECORDED", "SELL"), ("DELETED", "DISMISSED", "WAIT")]
    ]
    result = journal.build_recommendation_journal(rows, prices(), "2026-05-01")
    assert len(result["entries"]) == 4
    assert [row["status"] for row in result["entries"]] == ["OPEN", "DISMISSED", "RECORDED", "DISMISSED"]
    assert len({row["outcomes"][0]["stock_return"] for row in result["entries"][:3]}) == 1
    assert result["entries"][-1]["outcomes"][0]["stock_null_reason"] == "missing_history"
    assert "DELETED" in result["needed_symbols"]
    assert "Not a strategy backtest or unbiased performance estimate" in result["methodology"]["bias_warning"]
    assert "trading P&L" in result["methodology"]["execution"]


@pytest.mark.parametrize("overrides,reason", [
    ({"generated_at": None}, "invalid_recorded_timing"),
    ({"generated_at": "2026-01-05"}, "invalid_recorded_timing"),
    ({"market_as_of": "not a date"}, "invalid_recorded_timing"),
    ({"market_as_of": "2026-01-06"}, "signal_date_after_generation"),
    ({"generated_at": "2026-11-01T22:00:00Z"}, "generation_not_yet_observable"),
    ({"market_as_of": "2024-01-05", "generated_at": "2024-01-05T22:00:00Z"}, "calendar_unavailable"),
])
def test_invalid_timing_and_unsupported_calendar_do_not_fabricate_outcomes(overrides, reason):
    result = journal.build_recommendation_journal(history(**overrides), prices())
    assert first(result)["null_reason"] == reason
    assert first(result)["stock_return"] is None


def test_no_stale_substitution_or_future_data_leak_and_intraday_cutoff(monkeypatch):
    frames = prices()
    before = journal.build_recommendation_journal(history(), frames, "2026-01-12")
    frames["AAA"].loc["2026-01-13":, "Close"] = 99999
    assert journal.build_recommendation_journal(history(), frames, "2026-01-12") == before
    monkeypatch.setattr(journal, "_now", lambda: datetime(2026, 1, 13, 18, tzinfo=timezone.utc))
    result = journal.build_recommendation_journal(history(), frames, "2099-01-01")
    assert result["asof"] == "2026-01-12"
    assert first(result)["status"] == "maturing"


def test_fetch_request_is_bounded_keeps_deferred_rows_and_accepts_loader_shape():
    rows = history()
    rows[0]["recommendations"].append({"ticker": "ZZZ", "status": "DISMISSED"})
    result = journal.build_recommendation_journal(rows, max_symbols=2)
    assert result["fetch_request"] == {"tickers": ["SPY", "AAA"], "start_date": "2026-01-06", "end_date": "2026-10-23"}
    assert result["deferred_symbols"] == ["ZZZ"]
    assert len(result["entries"]) == 2
    assert result["entries"][1]["outcomes"][0]["null_reason"] == "symbol_limit"
    assert journal.build_recommendation_journal([])["fetch_request"] is None
    with pytest.raises(ValueError):
        journal.build_recommendation_journal(rows, max_symbols=51)


def test_timezone_daily_labels_remain_exact_dates():
    frames = prices()
    for frame in frames.values():
        frame.index = frame.index.tz_localize("UTC")
    assert first(journal.build_recommendation_journal(history(), frames, "2026-01-13"))["status"] == "complete"


def test_recorded_signal_price_and_later_quotes_cannot_replace_missing_reference():
    rows, frames = history(), prices()
    rows[0]["recommendations"][0]["price"] = 0.01
    frames["AAA"] = frames["AAA"].drop(pd.Timestamp("2026-01-06"))
    outcome = first(journal.build_recommendation_journal(rows, frames, "2026-05-01"))
    assert outcome["stock_return"] is None
    assert outcome["stock_null_reason"] == "missing_session:2026-01-06"


def test_stale_tail_is_missing_not_shortened_or_maturing():
    frames = prices()
    frames["AAA"] = frames["AAA"].loc[:"2026-01-12"]
    outcome = first(journal.build_recommendation_journal(history(), frames, "2026-05-01"))
    assert outcome["status"] == "unavailable"
    assert outcome["stock_null_reason"] == "missing_session:2026-01-13"
    assert outcome["end_date"] == "2026-01-13"
    assert outcome["spy_return"] is not None
