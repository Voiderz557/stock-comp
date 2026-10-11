"""Read-only market watcher regression tests (no provider or live account)."""

from datetime import datetime, timezone
from pathlib import Path
from contextlib import closing
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pandas as pd
from streamlit.testing.v1 import AppTest

from data.ticker_history import (
    NOT_PUBLIC_YET,
    lifecycle_status_for_period,
    listed_price_range,
    provider_fetch_segments,
)
from paper_trading import storage
from paper_trading.market_watcher import (
    evaluate_observation,
    load_watcher_history,
    run_watcher_check,
)
from paper_trading.plan_store import save_plan


NOW = datetime(2026, 10, 7, 12, tzinfo=timezone.utc)


def _frame(last=100.0, last_volume=1000):
    dates = pd.bdate_range("2026-09-07", periods=22)
    return pd.DataFrame(
        {"Close": [100.0] * 21 + [last], "Volume": [1000] * 17 + [last_volume] * 5},
        index=dates,
    )


def _plan():
    return {
        "mode": "Baseline", "strategy_name": "Baseline", "strategy_reason": "test",
        "regime_name": None, "price_source": "daily close", "buys": [
            {"Ticker": "AAA", "Signal": "BUY", "Score": 1.0, "Price": 100.0,
             "Price As Of": "2026-10-06", "Quantity": 1.0, "Allocation": 100.0,
             "Reason": "test"}
        ],
        "holdings": [], "cash_after": 99_900.0,
        "remaining_capacity_before": 100_000.0,
        "remaining_capacity_after": 99_900.0, "data_warnings": [],
    }


def test_price_volume_and_missing_data_are_explicit():
    account = {"Cash": 100_000.0, "Starting Capital": 100_000.0}
    plan = {"id": 1, "strategy_name": None, "buys": [{"Ticker": "AAA"}, {"Ticker": "BBB"}]}
    snapshot, alerts = evaluate_observation(
        account, [], plan, None, {"AAA": _frame(110, 10_000)}, now=NOW
    )
    assert snapshot["tickers"]["BBB"]["price"] is None
    assert {item["code"] for item in alerts} >= {"price_move", "volume_surge", "missing_price"}
    assert next(item for item in alerts if item["code"] == "price_move")["price_as_of"]


def test_future_rows_cannot_change_historical_observation():
    account = {"Cash": 100_000.0, "Starting Capital": 100_000.0}
    plan = {"id": 1, "strategy_name": None, "buys": [{"Ticker": "AAA"}]}
    earlier = datetime(2026, 9, 30, 12, tzinfo=timezone.utc)
    frame = _frame()
    changed = pd.concat([
        frame, pd.DataFrame(
            {"Close": [100_000.0], "Volume": [1_000_000]},
            index=pd.DatetimeIndex(["2026-10-08"]),
        ),
    ])
    before, _ = evaluate_observation(account, [], plan, None, {"AAA": frame}, now=earlier)
    after, _ = evaluate_observation(account, [], plan, None, {"AAA": changed}, now=earlier)
    assert before["tickers"] == after["tickers"]


def test_in_progress_daily_bar_uses_prior_completed_close_for_quote_and_signal():
    account = {"Cash": 100_000.0, "Starting Capital": 100_000.0}
    plan = {"id": 1, "strategy_name": "Baseline", "buys": [{"Ticker": "AAA"}]}
    frame = pd.concat([
        _frame(last=110),
        pd.DataFrame(
            {"Close": [150.0], "Volume": [1_000_000]},
            index=pd.DatetimeIndex(["2026-10-07"]),
        ),
    ])
    observed_frames = []

    def analyze(_definition, _ticker, frames):
        observed_frames.append(frames["AAA"].copy())
        return {"Signal": "BUY"}

    with patch("paper_trading.market_watcher.analyze_ticker", side_effect=analyze):
        snapshot, alerts = evaluate_observation(
            account, [], plan, None, {"AAA": frame}, now=NOW
        )

    assert snapshot["tickers"]["AAA"]["price"] == 110.0
    assert pd.Timestamp(snapshot["tickers"]["AAA"]["price_as_of"]).date().isoformat() == "2026-10-06"
    assert observed_frames[0].index.max() == pd.Timestamp("2026-10-06")
    assert not any(item["code"] == "price_move" and "50.0%" in item["message"] for item in alerts)


def test_same_day_bar_is_available_after_new_york_close_but_future_bar_is_not():
    account = {"Cash": 100_000.0, "Starting Capital": 100_000.0}
    plan = {"id": 1, "strategy_name": None, "buys": [{"Ticker": "AAA"}]}
    frame = pd.concat([
        _frame(last=110),
        pd.DataFrame(
            {"Close": [120.0, 999.0], "Volume": [1_000, 1_000]},
            index=pd.DatetimeIndex(["2026-10-07", "2026-10-08"]),
        ),
    ])
    after_close = datetime(2026, 10, 7, 21, tzinfo=timezone.utc)
    snapshot, _ = evaluate_observation(account, [], plan, None, {"AAA": frame}, now=after_close)
    assert snapshot["tickers"]["AAA"]["price"] == 120.0
    assert pd.Timestamp(snapshot["tickers"]["AAA"]["price_as_of"]).date().isoformat() == "2026-10-07"


def test_aware_daily_index_uses_its_session_date_before_new_york_close():
    account = {"Cash": 100_000.0, "Starting Capital": 100_000.0}
    plan = {"id": 1, "strategy_name": None, "buys": [{"Ticker": "AAA"}]}
    frame = pd.DataFrame(
        {"Close": [100.0, 150.0], "Volume": [1_000, 1_000]},
        index=pd.DatetimeIndex(["2026-10-06", "2026-10-07"], tz="America/New_York"),
    )
    snapshot, _ = evaluate_observation(account, [], plan, None, {"AAA": frame}, now=NOW)
    assert snapshot["tickers"]["AAA"]["price"] == 100.0


def test_only_in_progress_bar_reports_missing_close_without_signal():
    account = {"Cash": 100_000.0, "Starting Capital": 100_000.0}
    plan = {"id": 1, "strategy_name": "Baseline", "buys": [{"Ticker": "AAA"}]}
    frame = pd.DataFrame(
        {"Close": [150.0], "Volume": [1_000]},
        index=pd.DatetimeIndex(["2026-10-07"]),
    )
    with patch("paper_trading.market_watcher.analyze_ticker") as analyze:
        snapshot, alerts = evaluate_observation(
            account, [], plan, None, {"AAA": frame}, now=NOW
        )
    assert snapshot["tickers"]["AAA"] == {
        "price": None, "price_as_of": None, "signal": None
    }
    assert "missing_price" in {item["code"] for item in alerts}
    analyze.assert_not_called()


def test_aware_listing_requests_keep_calendar_boundaries_and_naive_provider_dates():
    start = pd.Timestamp("2023-09-13 15:00", tz="America/New_York")
    end = pd.Timestamp("2023-09-16 09:00", tz="America/New_York")
    assert listed_price_range("ARM", start, end) == (
        pd.Timestamp("2023-09-14"), pd.Timestamp("2023-09-16")
    )
    assert lifecycle_status_for_period(
        "ARM", pd.Timestamp("2023-09-12", tz="UTC"),
        pd.Timestamp("2023-09-13", tz="UTC"),
    ) == NOT_PUBLIC_YET
    assert provider_fetch_segments(
        "FISV", pd.Timestamp("2023-06-06", tz="UTC"),
        pd.Timestamp("2023-06-09", tz="UTC"),
    ) == [
        ("FISV", pd.Timestamp("2023-06-06"), pd.Timestamp("2023-06-07")),
        ("FI", pd.Timestamp("2023-06-07"), pd.Timestamp("2023-06-09")),
    ]


def test_check_persists_only_separate_watcher_history_and_deduplicates():
    with TemporaryDirectory() as directory:
        ledger = Path(directory) / "paper_portfolio.sqlite3"
        watcher = Path(directory) / "market_watcher.sqlite3"
        storage.initialize_storage(ledger)
        save_plan(_plan(), storage.get_account_state(ledger), [], db_path=ledger)
        account_before = storage.get_account_state(ledger)
        positions_before = storage.get_open_positions(ledger)
        plans_before = ledger.read_bytes()
        calls = []

        def loader(tickers, start, end):
            calls.append(tuple(tickers))
            assert start.tzinfo is None and end.tzinfo is None
            assert end == pd.Timestamp("2026-10-06")
            return {"AAA": _frame(110, 10_000)}, {"Data Source Failures": []}

        with patch("paper_trading.market_watcher.analyze_ticker", return_value={"Signal": "BUY"}):
            first = run_watcher_check(
                paper_db_path=ledger, watcher_path=watcher, load_market_data_fn=loader, now=NOW
            )
            second = run_watcher_check(
                paper_db_path=ledger, watcher_path=watcher, load_market_data_fn=loader, now=NOW
            )
        assert calls == [("AAA",), ("AAA",)]
        assert first["new_alerts"]
        assert second["new_alerts"] == []
        assert load_watcher_history(watcher)["last_observation"]["plan_id"] == 1
        assert storage.get_account_state(ledger) == account_before
        assert storage.get_open_positions(ledger) == positions_before
        assert ledger.read_bytes() == plans_before


def test_missing_prices_block_capacity_inference_for_holdings():
    account = {"Cash": 10_000.0, "Starting Capital": 100_000.0}
    positions = [{"id": 1, "ticker": "MISSING", "direction": "LONG", "quantity": 2.0}]
    _, alerts = evaluate_observation(account, positions, None, None, {}, now=NOW)
    assert {item["code"] for item in alerts} >= {"missing_price", "capacity_unavailable"}


def test_old_saved_plan_and_changed_signal_are_flagged():
    account = {"Cash": 100_000.0, "Starting Capital": 100_000.0}
    plan = {
        "id": 7, "strategy_name": "Baseline", "price_session_end": "2026-09-01",
        "buys": [{"Ticker": "AAA"}],
        "account_snapshot": {"Cash": 100_000.0, "Open Position IDs": []},
    }
    prior = {
        "plan_id": 7, "strategy_name": "Baseline",
        "tickers": {"AAA": {"signal": "WAIT"}},
    }
    with patch("paper_trading.market_watcher.analyze_ticker", return_value={"Signal": "BUY"}):
        _, alerts = evaluate_observation(
            account, [], plan, None, {"AAA": _frame()}, previous=prior, now=NOW
        )
    assert {item["code"] for item in alerts} >= {"old_plan", "signal_change"}


def test_concurrent_account_change_aborts_without_saving():
    with TemporaryDirectory() as directory:
        ledger = Path(directory) / "paper_portfolio.sqlite3"
        watcher = Path(directory) / "market_watcher.sqlite3"
        storage.initialize_storage(ledger)
        save_plan(_plan(), storage.get_account_state(ledger), [], db_path=ledger)

        def loader(tickers, start, end):
            with closing(storage._connect(ledger)) as connection:
                with connection:
                    connection.execute("UPDATE account_state SET cash = cash - 1 WHERE id = 1")
            return {"AAA": _frame()}, {"Data Source Failures": []}

        import pytest

        with pytest.raises(RuntimeError, match="changed during"):
            run_watcher_check(
                paper_db_path=ledger, watcher_path=watcher,
                load_market_data_fn=loader, now=NOW,
            )
        assert not watcher.exists()


def test_no_history_read_does_not_create_watcher_database():
    with TemporaryDirectory() as directory:
        path = Path(directory) / "market_watcher.sqlite3"
        assert load_watcher_history(path) == {"last_observation": None, "alerts": []}
        assert not path.exists()


def test_dashboard_check_now_and_restart_restore_alerts():
    dashboard = Path(__file__).resolve().parents[1] / "app" / "competition_dashboard.py"
    with TemporaryDirectory() as directory:
        ledger = Path(directory) / "paper_portfolio.sqlite3"
        storage.initialize_storage(ledger)
        save_plan(_plan(), storage.get_account_state(ledger), [], db_path=ledger)
        with patch("paper_trading.storage.DEFAULT_DB_PATH", str(ledger)), patch(
            "data.market_data.load_market_data",
            return_value=({"AAA": _frame(110, 10_000)}, {"Data Source Failures": []}),
        ), patch("paper_trading.market_watcher.analyze_ticker", return_value={"Signal": "BUY"}):
            app = AppTest.from_file(str(dashboard)).run(timeout=60)
            assert not app.exception
            check = [item for item in app.button if item.label == "Check market changes now"]
            assert len(check) == 1
            check[0].click()
            app.run(timeout=60)
            assert not app.exception
            history = load_watcher_history(Path(directory) / "market_watcher.sqlite3")
            assert history["alerts"]
            restarted = AppTest.from_file(str(dashboard)).run(timeout=60)
            assert not restarted.exception
            assert any("Last check:" in item.value for item in restarted.caption)
