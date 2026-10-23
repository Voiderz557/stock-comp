"""Service tests use fake loaders only: no network, account, or disk cache I/O."""

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from threading import BoundedSemaphore, Event

import pandas as pd
import pytest

from api import journal_service as service
from api import recommendation_journal as journal


@pytest.fixture(autouse=True)
def isolated_service(monkeypatch):
    monkeypatch.setattr(service, "_LOAD_SLOT", BoundedSemaphore(1))
    monkeypatch.setattr(service, "PRICE_LOAD_TIMEOUT_SECONDS", 1)
    monkeypatch.setattr(journal, "_now", lambda: datetime(2026, 10, 23, 22, tzinfo=timezone.utc))


def history(owner="one", ticker="AAA"):
    return [{"id": owner, "generated_at": "2026-01-05T22:00:00Z",
             "market_as_of": "2026-01-05", "recommendations": [
                 {"ticker": ticker, "status": "DISMISSED", "reason": owner},
             ]}]


def frames(tickers):
    dates = pd.bdate_range("2026-01-06", "2026-05-01")
    dates = dates.difference(pd.to_datetime(["2026-01-19", "2026-02-16", "2026-04-03"]))
    return {ticker: pd.DataFrame({"Close": [100 + n for n in range(len(dates))]}, index=dates)
            for ticker in tickers}


def assert_null(result):
    assert all(outcome["stock_return"] is None and outcome["spy_return"] is None
               for entry in result["entries"] for outcome in entry["outcomes"])


async def until(predicate):
    for _ in range(500):
        if predicate():
            return
        await asyncio.sleep(.002)
    pytest.fail("Worker did not reach the expected state")


def slot_available():
    if not service._LOAD_SLOT.acquire(blocking=False):
        return False
    service._LOAD_SLOT.release()
    return True


def test_uses_exact_bounded_fetch_request_existing_cache_defaults_and_preserves_source(monkeypatch):
    calls = []

    def loader(tickers, start, end, **kwargs):
        calls.append((tickers, start, end, kwargs))
        return frames(tickers), {}

    monkeypatch.setattr(service, "load_market_data", loader)
    rows = history()
    rows[0]["recommendations"] += [{"ticker": f"T{n:03d}", "status": "RECORDED"} for n in range(60)]
    original = deepcopy(rows)
    result = asyncio.run(service.load_journal(rows))
    request = result["fetch_request"]
    assert calls[0][:3] == (request["tickers"], request["start_date"], request["end_date"])
    assert calls[0][3]["max_workers"] == 1
    assert callable(calls[0][3]["status_callback"])
    assert set(calls[0][3]) == {"max_workers", "status_callback"}
    assert len(calls[0][0]) == 50 and calls[0][0][0] == "SPY"
    assert len(result["entries"]) == 61
    assert len(result["deferred_symbols"]) == 12
    assert result["warnings"]
    assert rows == original
    assert slot_available()


def test_success_and_sequential_accounts_do_not_share_journal_results(monkeypatch):
    calls = []

    def loader(tickers, *_args, **_kwargs):
        calls.append(list(tickers))
        return frames(tickers), {}

    monkeypatch.setattr(service, "load_market_data", loader)
    first = asyncio.run(service.load_journal(history("private-one")))
    second = asyncio.run(service.load_journal(history("private-two")))
    assert len(calls) == 2  # No result cache or cross-account singleflight.
    assert first["price_load_status"] == second["price_load_status"] == "loaded"
    assert first["warnings"] == second["warnings"] == []
    assert "private-one" not in str(second)
    assert "private-two" not in str(first)
    assert second["entries"][0]["outcomes"][0]["stock_return"] == pytest.approx(.05)


@pytest.mark.parametrize("rows", [[], history()])
def test_no_fetch_request_skips_loader(monkeypatch, rows):
    if rows:
        rows[0]["generated_at"] = None
    monkeypatch.setattr(service, "load_market_data", lambda *_args, **_kwargs: pytest.fail("Unexpected load"))
    result = asyncio.run(service.load_journal(rows))
    assert result["price_load_status"] == "not_needed"
    assert result["warnings"] == []


@pytest.mark.parametrize("mode", ["raises", "bad_tuple", "bad_prices", "bad_report"])
def test_failures_return_null_outcomes_and_sanitized_warning_and_release_slot(monkeypatch, mode):
    def loader(*_args, **_kwargs):
        if mode == "raises":
            raise RuntimeError("secret-token private-account provider-url")
        return {"bad_tuple": None, "bad_prices": ([], {}), "bad_report": ({}, None)}[mode]

    monkeypatch.setattr(service, "load_market_data", loader)
    result = asyncio.run(service.load_journal(history()))
    assert result["price_load_status"] == "unavailable"
    assert result["warnings"] and "secret-token" not in str(result)
    assert_null(result)
    assert slot_available()


def test_partial_price_failure_preserves_rows_and_valid_matched_outcomes(monkeypatch):
    monkeypatch.setattr(service, "load_market_data", lambda *_args, **_kwargs: (
        frames(["SPY", "AAA"]), {"Data Source Failures": [{"Reason": "secret-provider-detail"}]},
    ))
    rows = history()
    rows[0]["recommendations"].append({"ticker": "DELETED", "status": "RECORDED"})
    result = asyncio.run(service.load_journal(rows))
    assert result["price_load_status"] == "partial"
    assert result["entries"][0]["outcomes"][0]["status"] == "complete"
    assert result["entries"][1]["outcomes"][0]["stock_return"] is None
    assert result["entries"][1]["status"] == "RECORDED"
    assert result["warnings"] and "secret-provider-detail" not in str(result)


def test_timeout_retains_slot_even_after_request_loop_closes(monkeypatch):
    release = Event()
    calls = []

    def loader(*_args, **_kwargs):
        calls.append(True)
        assert release.wait(3)
        raise RuntimeError("late private provider failure")

    monkeypatch.setattr(service, "load_market_data", loader)
    monkeypatch.setattr(service, "PRICE_LOAD_TIMEOUT_SECONDS", .02)
    try:
        result = asyncio.run(service.load_journal(history("first")))
        assert result["price_load_status"] == "timed_out"
        assert_null(result)
        assert not slot_available()
        other = asyncio.run(service.load_journal(history("second")))
        assert other["price_load_status"] == "busy"
        assert "first" not in str(other)
        assert_null(other)
        assert len(calls) == 1
    finally:
        release.set()
        asyncio.run(until(slot_available))
    monkeypatch.setattr(service, "load_market_data", lambda tickers, *_a, **_k: (frames(tickers), {}))
    monkeypatch.setattr(service, "PRICE_LOAD_TIMEOUT_SECONDS", 1)
    assert asyncio.run(service.load_journal(history()))["price_load_status"] == "loaded"


def test_cancellation_keeps_slot_and_late_errors_are_consumed(monkeypatch):
    entered, release = Event(), Event()

    def loader(*_args, **_kwargs):
        entered.set()
        assert release.wait(3)
        raise RuntimeError("late error")

    monkeypatch.setattr(service, "load_market_data", loader)

    async def scenario():
        errors = []
        asyncio.get_running_loop().set_exception_handler(lambda _loop, context: errors.append(context))
        task = asyncio.create_task(service.load_journal(history()))
        try:
            await until(entered.is_set)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert not slot_available()
            assert (await service.load_journal(history("other")))["price_load_status"] == "busy"
        finally:
            release.set()
            await until(slot_available)
            await asyncio.sleep(.01)
        assert errors == []

    asyncio.run(scenario())


def test_input_snapshot_is_frozen_during_load(monkeypatch):
    entered, release = Event(), Event()

    def loader(tickers, *_args, **_kwargs):
        entered.set()
        assert release.wait(3)
        return frames(tickers), {}

    monkeypatch.setattr(service, "load_market_data", loader)

    async def scenario():
        rows = history("original")
        task = asyncio.create_task(service.load_journal(rows))
        try:
            await until(entered.is_set)
            rows[0]["recommendations"][0]["reason"] = "changed"
        finally:
            release.set()
        result = await task
        assert result["entries"][0]["recommendation"]["reason"] == "original"

    asyncio.run(scenario())


def test_submission_failure_releases_slot(monkeypatch):
    class StoppedExecutor:
        def submit(self, *_args):
            raise RuntimeError("shut down")

    monkeypatch.setattr(service, "_EXECUTOR", StoppedExecutor())
    result = asyncio.run(service.load_journal(history()))
    assert result["price_load_status"] == "unavailable"
    assert_null(result)
    assert slot_available()


def test_empty_prices_warn_even_while_all_horizons_are_maturing(monkeypatch):
    monkeypatch.setattr(service, "load_market_data", lambda tickers, *_a, **_k: (
        {ticker: pd.DataFrame() for ticker in tickers}, {},
    ))
    rows = history()
    rows[0].update(generated_at="2026-10-22T22:00:00Z", market_as_of="2026-10-22")
    result = asyncio.run(service.load_journal(rows))
    assert result["price_load_status"] == "partial"
    assert result["warnings"]
    assert all(item["status"] == "maturing" for item in result["entries"][0]["outcomes"])


@pytest.mark.parametrize("expire_before_first", [False, True])
def test_deadline_callback_stops_remaining_tickers_and_sanitizes_error(monkeypatch, expire_before_first):
    clock = [100.0]
    loaded, errors = [], []
    monkeypatch.setattr(service, "monotonic", lambda: clock[0])

    def loader(tickers, *_args, status_callback, **_kwargs):
        if expire_before_first:
            clock[0] = 101.0  # e.g. shared cache lock was held by planning.
        for ticker in tickers:
            try:
                status_callback("DOWNLOADING", "private provider text")
            except TimeoutError as exc:
                errors.append(str(exc))
                raise
            loaded.append(ticker)
            clock[0] = 101.0  # The current ticker used the remaining budget.
        pytest.fail("Loader must stop at the deadline before remaining work")

    monkeypatch.setattr(service, "load_market_data", loader)
    result = asyncio.run(service.load_journal(history()))
    assert loaded == ([] if expire_before_first else ["SPY"])
    assert errors == ["Journal price loading deadline exceeded."]
    assert result["price_load_status"] == "timed_out"
    assert "private provider text" not in str(result)
    assert_null(result)
    assert slot_available()


def test_real_sequential_loader_checks_callback_before_next_ticker_and_releases_cache_lock(monkeypatch, tmp_path):
    from data import market_data

    clock, loaded = [100.0], []
    monkeypatch.setattr(service, "monotonic", lambda: clock[0])
    monkeypatch.setattr(market_data, "_resolve_cache_dir", lambda _path: tmp_path)

    def ticker_loader(ticker, *_args, **_kwargs):
        loaded.append(ticker)
        clock[0] = 101.0
        return pd.DataFrame(), False, [], []

    monkeypatch.setattr(market_data, "_load_ticker", ticker_loader)
    result = asyncio.run(service.load_journal(history()))
    assert loaded == ["SPY"]
    assert result["price_load_status"] == "timed_out"
    assert_null(result)
    assert slot_available()
    # Re-enter the same real loader/lock from this thread after unwinding.
    # An empty load requires no provider and confirms the lock was released.
    data, _report = market_data.load_market_data([], "2026-01-06", "2026-01-13")
    assert data == {}
