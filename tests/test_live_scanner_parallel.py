"""Live-loader concurrency must preserve historical loader semantics."""

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from time import sleep

import pandas as pd
import pytest

from data.market_data import LIVE_FAILURE_CACHE_NAME, FAILURE_CACHE_NAME, load_market_data
from paper_trading.trading_plan import LIVE_SCANNER_PRICE_WORKERS


def _frame(ticker):
    return pd.DataFrame(
        {"Close": [10.0 + len(ticker)], "Volume": [1000]},
        index=pd.DatetimeIndex(["2026-10-02"]),
    )


def test_bounded_parallel_loader_preserves_order_prices_and_cache(tmp_path, monkeypatch):
    guard = Lock()
    active = 0
    peak = 0
    requested = []

    def download(ticker, start, end):
        nonlocal active, peak
        with guard:
            requested.append(ticker)
            active += 1
            peak = max(peak, active)
        sleep(0.03)
        with guard:
            active -= 1
        return _frame(ticker)

    monkeypatch.setattr("data.market_data._download_with_hard_timeout", download)
    symbols = ["AAA", "BBBB", "CC", "DDDD", "EEEE", "FFFF"]
    serial, _ = load_market_data(symbols, "2026-10-01", "2026-10-03", cache_dir=tmp_path / "serial")
    peak = 0
    progress = []
    parallel, info = load_market_data(
        symbols, "2026-10-01", "2026-10-03", cache_dir=tmp_path / "parallel",
        max_workers=LIVE_SCANNER_PRICE_WORKERS,
        status_callback=lambda stage, detail: progress.append((stage, detail)),
    )
    assert 2 <= peak <= LIVE_SCANNER_PRICE_WORKERS
    assert list(parallel) == symbols == list(serial)
    assert all(parallel[ticker].equals(serial[ticker]) for ticker in symbols)
    assert info["Downloaded Tickers"] == symbols
    assert len(progress) == len(symbols)
    assert progress[-1][1].endswith(f"({len(symbols)}/{len(symbols)})")
    requested.clear()
    warm, warm_info = load_market_data(
        symbols, "2026-10-01", "2026-10-03", cache_dir=tmp_path / "parallel",
        max_workers=LIVE_SCANNER_PRICE_WORKERS,
    )
    assert requested == []
    assert warm_info["Downloaded Tickers"] == []
    assert all(warm[ticker].equals(parallel[ticker]) for ticker in symbols)


def test_parallel_failure_cache_prevents_repeat_requests(tmp_path, monkeypatch):
    calls = []

    def download(ticker, *_args):
        calls.append(ticker)
        return pd.DataFrame() if ticker == "BAD" else _frame(ticker)

    monkeypatch.setattr("data.market_data._download_with_hard_timeout", download)
    for iteration in range(2):
        data, report = load_market_data(
            ["AAA", "BAD", "CC"], "2026-10-01", "2026-10-03",
            cache_dir=tmp_path, max_workers=3,
        )
        assert data["BAD"].empty
        assert [item["Ticker"] for item in report["Data Source Failures"]] == ["BAD"]
        if iteration:
            assert report["Data Source Failures"][0]["Cached Failure"] is True
    assert calls.count("BAD") == 1


def test_two_scans_sharing_cache_do_not_duplicate_provider_requests(tmp_path, monkeypatch):
    guard = Lock()
    calls = []

    def download(ticker, *_args):
        with guard:
            calls.append(ticker)
        sleep(0.03)
        return _frame(ticker)

    monkeypatch.setattr("data.market_data._download_with_hard_timeout", download)

    def scan():
        return load_market_data(
            ["AAA", "BBB", "CCC"], "2026-10-01", "2026-10-03",
            cache_dir=tmp_path, max_workers=3,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(scan)
        second = executor.submit(scan)
        first.result()
        second.result()
    assert sorted(calls) == ["AAA", "BBB", "CCC"]


def test_live_failures_are_range_cached_briefly_but_not_permanent(tmp_path, monkeypatch):
    calls = []

    def empty_download(ticker, *_args):
        calls.append(ticker)
        return pd.DataFrame()

    monkeypatch.setattr("data.market_data._download_with_hard_timeout", empty_download)
    def scan():
        return load_market_data(
            ["BAD"], "2026-10-01", "2026-10-03", cache_dir=tmp_path,
            max_workers=4, live_failure_retry=True,
        )[1]

    first = scan()
    second = scan()
    assert calls == ["BAD"]
    assert second["Data Source Failures"][0]["Cached Failure"] is True
    assert not (tmp_path / FAILURE_CACHE_NAME).exists()
    path = tmp_path / LIVE_FAILURE_CACHE_NAME
    records = json.loads(path.read_text(encoding="utf-8"))
    records[0]["Timestamp"] = "2020-01-01T00:00:00+00:00"
    path.write_text(json.dumps(records), encoding="utf-8")
    third = scan()
    assert calls == ["BAD", "BAD"]
    assert third["Data Source Failures"][0].get("Cached Failure") is not True
    assert first["Failure Cache Policy"] == "15-minute live retry"


def test_parallel_loader_rejects_unbounded_workers_and_unshared_provider(tmp_path):
    with pytest.raises(ValueError, match="between 1 and 8"):
        load_market_data([], "2026-10-01", "2026-10-03", cache_dir=tmp_path, max_workers=100)
    with pytest.raises(ValueError, match="Custom secondary providers"):
        load_market_data([], "2026-10-01", "2026-10-03", cache_dir=tmp_path, max_workers=2, secondary_providers=[])
