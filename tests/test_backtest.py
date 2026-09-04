import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

from backtesting.engine import (
    download_backtest_data,
    rank_buy_candidates,
    rebalance_portfolio,
    run_backtest,
)
from backtesting.periods import generate_random_periods
from data.historical_universe import get_historical_universe
from data.market_data import LocalParquetProvider, load_market_data
from data.ticker_history import (
    DELISTED_LATER,
    MISSING_FROM_PROVIDER,
    NOT_PUBLIC_YET,
    SYMBOL_CHANGE,
    get_provider_symbol,
    lifecycle_status_for_period,
)


def price_frames(tickers, date="2025-01-06", price=100.0):
    index = pd.DatetimeIndex([pd.Timestamp("2025-01-03"), pd.Timestamp(date)])
    return {
        ticker: pd.DataFrame(
            {"Open": [price, price], "Close": [price, price]}, index=index
        )
        for ticker in tickers
    }


def cache_frame(start, end):
    index = pd.date_range(start, end, freq="B")
    return pd.DataFrame(
        {
            "Open": 10.0,
            "High": 11.0,
            "Low": 9.0,
            "Close": 10.5,
            "Volume": 1000,
        },
        index=index,
    )


class PeriodTests(unittest.TestCase):
    def test_periods_are_reproducible_and_inside_boundaries(self):
        arguments = ("5 months", "2022-01-01", "2025-12-31", 5, 42)
        first = generate_random_periods(*arguments)
        self.assertEqual(first, generate_random_periods(*arguments))
        self.assertEqual(len(set(first)), 5)
        for start, end in first:
            self.assertGreaterEqual(start, pd.Timestamp("2022-01-01"))
            self.assertLessEqual(end, pd.Timestamp("2025-12-31"))
            self.assertEqual(end, start + pd.DateOffset(months=5))

    def test_rejects_boundaries_narrower_than_duration(self):
        with self.assertRaisesRegex(ValueError, "too narrow"):
            generate_random_periods(
                "1 year", "2025-01-01", "2025-06-01", 1, 42
            )


class HistoricalUniverseTests(unittest.TestCase):
    def test_latest_snapshot_never_comes_from_the_future(self):
        before = get_historical_universe("2024-12-22")
        on_change = get_historical_universe("2024-12-23")
        self.assertLess(before.effective_date, pd.Timestamp("2024-12-23"))
        self.assertNotIn("PLTR", before.tickers)
        self.assertEqual(on_change.effective_date, pd.Timestamp("2024-12-23"))
        self.assertIn("PLTR", on_change.tickers)

    def test_off_cycle_change_uses_its_effective_date(self):
        self.assertNotIn("SHOP", get_historical_universe("2025-05-18").tickers)
        self.assertIn("SHOP", get_historical_universe("2025-05-19").tickers)


class CacheTests(unittest.TestCase):
    def test_full_cache_hit_does_not_download(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as cache_dir:
            with patch(
                "data.market_data._download_range",
                return_value=cache_frame("2025-01-01", "2025-01-10"),
            ) as download:
                _, first = load_market_data(
                    ["AAPL"], "2025-01-01", "2025-01-10", cache_dir=cache_dir
                )
                _, second = load_market_data(
                    ["AAPL"], "2025-01-01", "2025-01-10", cache_dir=cache_dir
                )
            self.assertEqual(download.call_count, 1)
            self.assertEqual(first["Status"], "DOWNLOADING")
            self.assertEqual(second["Status"], "USING CACHE")
            self.assertTrue((Path(cache_dir) / "AAPL.parquet").exists())

    def test_partial_cache_downloads_only_prefix_and_suffix(self):
        calls = []

        def fake_download(ticker, start, end):
            calls.append((start, end))
            return cache_frame(start, end - pd.Timedelta(days=1))

        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as cache_dir:
            with patch("data.market_data._download_range", side_effect=fake_download):
                load_market_data(
                    ["AAPL"], "2025-01-01", "2025-01-10", cache_dir=cache_dir
                )
                load_market_data(
                    ["AAPL"], "2024-12-20", "2025-01-15", cache_dir=cache_dir
                )
        self.assertEqual(
            calls,
            [
                (pd.Timestamp("2025-01-01"), pd.Timestamp("2025-01-11")),
                (pd.Timestamp("2024-12-20"), pd.Timestamp("2025-01-01")),
                (pd.Timestamp("2025-01-11"), pd.Timestamp("2025-01-16")),
            ],
        )

    def test_valid_delisted_ticker_failure_is_reported_without_crashing(self):
        required = {
            "EA": [(pd.Timestamp("2024-01-02"), pd.Timestamp("2024-01-10"))]
        }
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as cache_dir:
            with patch(
                "data.market_data._download_range",
                return_value=pd.DataFrame(),
            ):
                data, report = load_market_data(
                    ["EA"],
                    "2024-01-01",
                    "2024-01-10",
                    cache_dir=cache_dir,
                    required_ranges=required,
                    secondary_providers=[],
                )
        self.assertTrue(data["EA"].empty)
        self.assertEqual(report["Unavailable Valid Constituents"], ["EA"])
        failure = report["Data Source Failures"][0]
        self.assertEqual(failure["Data Status"], MISSING_FROM_PROVIDER)
        self.assertEqual(failure["Lifecycle Status"], DELISTED_LATER)
        self.assertIn("yfinance returned no rows", failure["Reason"])

    def test_persistent_failure_cache_prevents_same_yfinance_request(self):
        required = {
            "SPLK": [(pd.Timestamp("2023-06-01"), pd.Timestamp("2023-12-01"))]
        }
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as cache_dir:
            with patch(
                "data.market_data._download_range", return_value=pd.DataFrame()
            ) as download:
                load_market_data(
                    ["SPLK"],
                    "2023-05-16",
                    "2023-12-18",
                    cache_dir=cache_dir,
                    required_ranges=required,
                    secondary_providers=[],
                )
                _, second_report = load_market_data(
                    ["SPLK"],
                    "2023-05-16",
                    "2023-12-18",
                    cache_dir=cache_dir,
                    required_ranges=required,
                    secondary_providers=[],
                )
            self.assertEqual(download.call_count, 1)
            self.assertTrue((Path(cache_dir) / "data_failures.json").exists())
        self.assertTrue(second_report["Data Source Failures"][0]["Cached Failure"])

    def test_failure_cache_is_range_scoped(self):
        required = {
            "SPLK": [(pd.Timestamp("2023-01-01"), pd.Timestamp("2023-01-15"))]
        }
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as cache_dir:
            with patch(
                "data.market_data._download_range", return_value=pd.DataFrame()
            ) as download:
                load_market_data(
                    ["SPLK"],
                    "2023-01-01",
                    "2023-01-10",
                    cache_dir=cache_dir,
                    required_ranges=required,
                    secondary_providers=[],
                )
                load_market_data(
                    ["SPLK"],
                    "2023-01-05",
                    "2023-01-15",
                    cache_dir=cache_dir,
                    required_ranges=required,
                    secondary_providers=[],
                )
            self.assertEqual(download.call_count, 2)
            second_start = download.call_args_list[1].args[1]
            self.assertEqual(second_start, pd.Timestamp("2023-01-11"))

    def test_hard_timeout_becomes_a_failure_instead_of_hanging(self):
        def slow_download(*args):
            time.sleep(0.1)
            return pd.DataFrame()

        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as cache_dir:
            with patch(
                "data.market_data._download_range", side_effect=slow_download
            ), patch(
                "data.market_data.YFINANCE_HARD_TIMEOUT_SECONDS", 0.01
            ):
                _, report = load_market_data(
                    ["EA"],
                    "2024-01-01",
                    "2024-01-10",
                    cache_dir=cache_dir,
                    required_ranges={
                        "EA": [
                            (pd.Timestamp("2024-01-01"), pd.Timestamp("2024-01-10"))
                        ]
                    },
                    secondary_providers=[],
                )
        self.assertIn("exceeded", report["Data Source Failures"][0]["Reason"])

    def test_manual_provider_fills_yfinance_failure(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as root:
            root = Path(root)
            manual_dir = root / "manual"
            cache_dir = root / "cache"
            manual_dir.mkdir()
            cache_frame("2024-01-01", "2024-01-10").to_parquet(
                manual_dir / "EA.parquet"
            )
            provider = LocalParquetProvider(manual_dir)
            with patch(
                "data.market_data._download_range",
                return_value=pd.DataFrame(),
            ):
                data, report = load_market_data(
                    ["EA"],
                    "2024-01-01",
                    "2024-01-10",
                    cache_dir=cache_dir,
                    required_ranges={
                        "EA": [
                            (pd.Timestamp("2024-01-01"), pd.Timestamp("2024-01-10"))
                        ]
                    },
                    secondary_providers=[provider],
                )
        self.assertFalse(data["EA"].empty)
        self.assertEqual(report["Data Source Failures"], [])
        self.assertEqual(
            report["Secondary Sources Used"],
            [{"Ticker": "EA", "Source": "LOCAL PARQUET"}],
        )

    def test_stale_manifest_repairs_missing_cached_suffix(self):
        requested_start = pd.Timestamp("2022-10-22")
        requested_end = pd.Timestamp("2023-03-22")
        calls = []

        def fake_download(ticker, start, end_exclusive):
            calls.append((start, end_exclusive))
            return cache_frame(start, end_exclusive - pd.Timedelta(days=1))

        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as cache_dir:
            cache_dir = Path(cache_dir)
            cache_frame("2022-10-24", "2023-01-17").to_parquet(
                cache_dir / "SPY.parquet"
            )
            (cache_dir / "coverage.json").write_text(
                json.dumps(
                    {
                        "SPY": {
                            "start": "2022-10-22",
                            "end_exclusive": "2023-03-23",
                        }
                    }
                ),
                encoding="utf-8",
            )
            with patch(
                "data.market_data._download_range", side_effect=fake_download
            ):
                data, report = load_market_data(
                    ["SPY"],
                    requested_start,
                    requested_end,
                    cache_dir=cache_dir,
                    required_ranges={"SPY": [(requested_start, requested_end)]},
                    secondary_providers=[],
                )
            repaired_manifest = json.loads(
                (cache_dir / "coverage.json").read_text(encoding="utf-8")
            )

        self.assertEqual(
            calls,
            [(pd.Timestamp("2023-01-18"), pd.Timestamp("2023-03-23"))],
        )
        self.assertEqual(data["SPY"].index.max(), pd.Timestamp("2023-03-22"))
        self.assertEqual(
            repaired_manifest["SPY"]["end_exclusive"], "2023-03-23"
        )
        self.assertEqual(report["Status"], "DOWNLOADING")


class TickerIdentityTests(unittest.TestCase):
    def test_symbol_change_routes_to_current_provider_symbol(self):
        self.assertEqual(get_provider_symbol("FB"), "META")
        self.assertEqual(
            lifecycle_status_for_period("FB", "2022-01-01", "2022-06-08"),
            SYMBOL_CHANGE,
        )

    def test_not_public_yet_is_distinct_from_provider_failure(self):
        self.assertEqual(
            lifecycle_status_for_period("CRWV", "2024-01-01", "2024-12-31"),
            NOT_PUBLIC_YET,
        )


class BenchmarkIsolationTests(unittest.TestCase):
    @staticmethod
    def _cache_report():
        return {
            "Status": "USING CACHE",
            "Downloaded Tickers": [],
            "Cache Directory": "test-cache",
            "Data Source Failures": [],
            "Unavailable Valid Constituents": [],
            "Secondary Sources Used": [],
            "Ticker Classifications": {},
        }

    def test_missing_benchmark_fails_before_loading_constituents(self):
        report = {
            "Data Source Failures": [
                {"Ticker": "SPY", "Reason": "provider timeout"}
            ]
        }
        with patch(
            "backtesting.engine.load_market_data",
            return_value=({"SPY": pd.DataFrame()}, report),
        ) as loader:
            with self.assertRaisesRegex(
                RuntimeError, "SPY benchmark data unavailable.*provider timeout"
            ):
                download_backtest_data(
                    pd.Timestamp("2025-01-06"),
                    pd.Timestamp("2025-01-10"),
                    "SPY",
                )
        self.assertEqual(loader.call_count, 1)

    def test_constituent_failure_does_not_become_benchmark_failure(self):
        spy = cache_frame("2025-01-06", "2025-01-10")
        report = {
            "Status": "USING CACHE",
            "Data Source Failures": [
                {
                    "Ticker": "SPLK",
                    "Reason": "yfinance returned no rows",
                }
            ],
            "Unavailable Valid Constituents": ["SPLK"],
        }
        phases = []
        with patch(
            "backtesting.engine.download_backtest_data",
            return_value=({"SPY": spy}, report),
        ), patch("backtesting.engine.rank_buy_candidates", return_value=[]):
            result = run_backtest(
                "2025-01-06",
                "2025-01-10",
                phase_callback=phases.append,
            )
        self.assertEqual(result["Benchmark"], "SPY")
        self.assertEqual(
            phases,
            ["Loading market data...", "Running simulation...", "Complete"],
        )

    def test_missing_benchmark_reports_actual_provider_reason(self):
        report = {
            "Data Source Failures": [
                {"Ticker": "SPY", "Reason": "yfinance exceeded 12 seconds"}
            ]
        }
        with patch(
            "backtesting.engine.download_backtest_data",
            return_value=({"SPY": pd.DataFrame()}, report),
        ):
            with self.assertRaisesRegex(
                RuntimeError, "SPY benchmark data unavailable.*exceeded 12 seconds"
            ):
                run_backtest("2025-01-06", "2025-01-10")

    def test_weekend_boundaries_adjust_only_to_nearby_trading_dates(self):
        spy = cache_frame("2025-01-06", "2025-01-10")
        with patch(
            "backtesting.engine.download_backtest_data",
            return_value=({"SPY": spy}, self._cache_report()),
        ), patch("backtesting.engine.rank_buy_candidates", return_value=[]):
            result = run_backtest("2025-01-04", "2025-01-12")

        self.assertEqual(result["Requested Start"], pd.Timestamp("2025-01-04"))
        self.assertEqual(result["Requested End"], pd.Timestamp("2025-01-12"))
        self.assertEqual(result["Actual Start"], pd.Timestamp("2025-01-06"))
        self.assertEqual(result["Actual End"], pd.Timestamp("2025-01-10"))

    def test_missing_beginning_of_benchmark_history_fails_test(self):
        spy = cache_frame("2025-01-20", "2025-02-28")
        with patch(
            "backtesting.engine.download_backtest_data",
            return_value=({"SPY": spy}, self._cache_report()),
        ):
            with self.assertRaisesRegex(
                RuntimeError, "benchmark coverage is incomplete.*first row"
            ):
                run_backtest("2025-01-01", "2025-02-28")

    def test_missing_end_of_benchmark_history_fails_test(self):
        spy = cache_frame("2025-01-02", "2025-02-03")
        with patch(
            "backtesting.engine.download_backtest_data",
            return_value=({"SPY": spy}, self._cache_report()),
        ):
            with self.assertRaisesRegex(
                RuntimeError, "benchmark coverage is incomplete.*last row"
            ):
                run_backtest("2025-01-01", "2025-02-28")

    def test_five_month_period_cannot_become_three_week_simulation(self):
        spy = cache_frame("2025-06-02", "2025-06-25")
        with patch(
            "backtesting.engine.download_backtest_data",
            return_value=({"SPY": spy}, self._cache_report()),
        ):
            with self.assertRaisesRegex(
                RuntimeError, "benchmark coverage is incomplete"
            ):
                run_backtest("2025-01-25", "2025-06-25")

    def test_large_internal_benchmark_gap_fails_test(self):
        beginning = cache_frame("2025-01-02", "2025-01-17")
        ending = cache_frame("2025-02-17", "2025-02-28")
        spy = pd.concat([beginning, ending])
        with patch(
            "backtesting.engine.download_backtest_data",
            return_value=({"SPY": spy}, self._cache_report()),
        ):
            with self.assertRaisesRegex(
                RuntimeError, "benchmark coverage is incomplete.*day gap"
            ):
                run_backtest("2025-01-01", "2025-02-28")


class PortfolioRuleTests(unittest.TestCase):
    def test_rank_filters_low_prices_without_a_position_limit(self):
        data = price_frames(["A", "B", "C"], price=10.0)
        data["B"].loc[pd.Timestamp("2025-01-06"), "Open"] = 4.99

        def signal(ticker, historical):
            self.assertLess(historical.index.max(), pd.Timestamp("2025-01-06"))
            return {
                "Ticker": ticker,
                "Signal": "BUY",
                "Score": 1,
                "Long Momentum": 1,
            }

        strategy = SimpleNamespace(
            analyze=signal,
            rank_key=lambda result: (result["Score"], result["Long Momentum"]),
        )
        ranked = rank_buy_candidates(
            data,
            pd.Timestamp("2025-01-06"),
            universe=["A", "B", "C"],
            min_stock_price=5.0,
            strategy=strategy,
        )
        self.assertEqual([item["Ticker"] for item in ranked], ["A", "C"])

    def test_allocates_down_ranking_and_skips_missing_ticker(self):
        tickers = ["A", "B", "C", "D", "E", "F"]
        data = price_frames(tickers)
        selected = [
            {"Ticker": ticker, "Score": 10 - index, "Long Momentum": 1}
            for index, ticker in enumerate(tickers)
        ]
        holdings = {"MISSING": 10.0}
        trades = []
        cash = rebalance_portfolio(
            data,
            pd.Timestamp("2025-01-06"),
            selected,
            100_000.0,
            holdings,
            trades,
            0.0,
            max_position_value=20_000.0,
        )
        self.assertEqual(cash, 0.0)
        self.assertEqual(holdings["MISSING"], 10.0)
        self.assertEqual(
            [ticker for ticker in tickers if ticker in holdings], tickers[:5]
        )
        self.assertTrue(
            all(holdings[ticker] * 100 == 20_000 for ticker in tickers[:5])
        )

    def test_initial_allocation_mode_does_not_trim_appreciation(self):
        data = price_frames(["A"], price=150.0)
        holdings = {"A": 200.0}
        trades = []
        cash = rebalance_portfolio(
            data,
            pd.Timestamp("2025-01-06"),
            [{"Ticker": "A"}],
            0.0,
            holdings,
            trades,
            0.0,
            max_position_value=20_000.0,
            position_limit_mode="initial_allocation",
        )
        self.assertEqual(cash, 0.0)
        self.assertEqual(holdings["A"], 200.0)
        self.assertEqual(trades, [])


if __name__ == "__main__":
    unittest.main()
