import unittest
from dataclasses import dataclass
from typing import Callable

import pandas as pd

from paper_trading.portfolio import (
    compute_position_metrics,
    latest_close_prices,
    scan_for_candidates,
    summarize_portfolio,
)


def _price_frame(closes):
    return pd.DataFrame(
        {"Close": closes, "Open": closes, "Volume": [1_000] * len(closes)},
        index=pd.date_range("2025-01-01", periods=len(closes)),
    )


class LatestClosePricesTests(unittest.TestCase):
    def test_extracts_last_close_per_ticker(self):
        prices = latest_close_prices(
            {"AAPL": _price_frame([100.0, 101.0, 102.5]), "MSFT": _price_frame([50.0, 49.0])}
        )
        self.assertEqual(prices, {"AAPL": 102.5, "MSFT": 49.0})

    def test_skips_empty_or_missing_frames(self):
        prices = latest_close_prices(
            {"AAPL": _price_frame([]), "MSFT": None, "GOOG": pd.DataFrame()}
        )
        self.assertEqual(prices, {})

    def test_handles_none_input(self):
        self.assertEqual(latest_close_prices(None), {})


class PositionMetricsTests(unittest.TestCase):
    def test_long_position_profit(self):
        position = {
            "ticker": "AAPL",
            "direction": "LONG",
            "entry_price": 100.0,
            "quantity": 10.0,
            "allocated_capital": 1_000.0,
        }
        metrics = compute_position_metrics(position, current_price=110.0)
        self.assertAlmostEqual(metrics["Unrealized P&L"], 100.0)
        self.assertAlmostEqual(metrics["Market Value"], 1_100.0)
        self.assertAlmostEqual(metrics["Unrealized P&L %"], 0.10)

    def test_long_position_loss(self):
        position = {
            "ticker": "AAPL",
            "direction": "LONG",
            "entry_price": 100.0,
            "quantity": 10.0,
            "allocated_capital": 1_000.0,
        }
        metrics = compute_position_metrics(position, current_price=90.0)
        self.assertAlmostEqual(metrics["Unrealized P&L"], -100.0)
        self.assertAlmostEqual(metrics["Unrealized P&L %"], -0.10)

    def test_short_position_profit_when_price_falls(self):
        position = {
            "ticker": "AAPL",
            "direction": "SHORT",
            "entry_price": 100.0,
            "quantity": 10.0,
            "allocated_capital": 1_000.0,
        }
        metrics = compute_position_metrics(position, current_price=90.0)
        self.assertAlmostEqual(metrics["Unrealized P&L"], 100.0)

    def test_zero_allocation_does_not_divide_by_zero(self):
        position = {
            "ticker": "AAPL",
            "direction": "LONG",
            "entry_price": 100.0,
            "quantity": 0.0,
            "allocated_capital": 0.0,
        }
        metrics = compute_position_metrics(position, current_price=110.0)
        self.assertEqual(metrics["Unrealized P&L %"], 0.0)


class SummarizePortfolioTests(unittest.TestCase):
    def test_summary_combines_cash_and_open_positions(self):
        account_state = {"Cash": 50_000.0, "Realized P&L": 500.0, "Starting Capital": 100_000.0}
        open_positions = [
            {
                "id": 1,
                "ticker": "AAPL",
                "direction": "LONG",
                "entry_price": 100.0,
                "quantity": 100.0,
                "allocated_capital": 10_000.0,
            },
            {
                "id": 2,
                "ticker": "MSFT",
                "direction": "LONG",
                "entry_price": 200.0,
                "quantity": 50.0,
                "allocated_capital": 10_000.0,
            },
        ]
        current_prices = {"AAPL": 110.0, "MSFT": 190.0}
        summary = summarize_portfolio(account_state, open_positions, current_prices)

        # AAPL market value 11,000 (+1,000 pnl); MSFT market value 9,500 (-500 pnl).
        self.assertAlmostEqual(summary["Total Market Value"], 20_500.0)
        self.assertAlmostEqual(summary["Total Unrealized P&L"], 500.0)
        self.assertAlmostEqual(summary["Portfolio Value"], 70_500.0)
        self.assertAlmostEqual(summary["Total P&L"], 1_000.0)
        self.assertEqual(len(summary["Open Positions"]), 2)
        for row in summary["Open Positions"]:
            self.assertFalse(row["Price Unavailable"])

    def test_missing_price_falls_back_to_entry_price_and_flags_it(self):
        account_state = {"Cash": 90_000.0, "Realized P&L": 0.0, "Starting Capital": 100_000.0}
        open_positions = [
            {
                "id": 1,
                "ticker": "AAPL",
                "direction": "LONG",
                "entry_price": 100.0,
                "quantity": 100.0,
                "allocated_capital": 10_000.0,
            }
        ]
        summary = summarize_portfolio(account_state, open_positions, current_prices={})
        row = summary["Open Positions"][0]
        self.assertTrue(row["Price Unavailable"])
        self.assertAlmostEqual(row["Unrealized P&L"], 0.0)
        self.assertAlmostEqual(summary["Portfolio Value"], 100_000.0)

    def test_no_open_positions_returns_cash_only_value(self):
        account_state = {"Cash": 100_000.0, "Realized P&L": 0.0, "Starting Capital": 100_000.0}
        summary = summarize_portfolio(account_state, [], {})
        self.assertAlmostEqual(summary["Portfolio Value"], 100_000.0)
        self.assertEqual(summary["Open Positions"], [])


@dataclass
class _FakeStrategy:
    name: str
    analyze: Callable
    rank_key: Callable


def _fake_get_strategy(strategies):
    def getter(name):
        return strategies[name]

    return getter


class ScanForCandidatesTests(unittest.TestCase):
    def test_only_buy_signals_are_returned_and_ranked(self):
        def analyze(ticker, data):
            price = float(data["Close"].iloc[-1])
            if ticker == "LOSER":
                return {"Ticker": ticker, "Score": -1, "Signal": "AVOID", "Price": price}
            if ticker == "FLAT":
                return {"Ticker": ticker, "Score": 0, "Signal": "WAIT", "Price": price}
            score = price  # Higher price -> higher score, purely for test ordering.
            return {"Ticker": ticker, "Score": score, "Signal": "BUY", "Price": price}

        strategy = _FakeStrategy(
            name="Fake", analyze=analyze, rank_key=lambda result: result["Score"]
        )
        universe = ["WINNER_LOW", "WINNER_HIGH", "LOSER", "FLAT"]
        price_data = {
            "WINNER_LOW": _price_frame([50.0]),
            "WINNER_HIGH": _price_frame([150.0]),
            "LOSER": _price_frame([10.0]),
            "FLAT": _price_frame([20.0]),
        }
        results = scan_for_candidates(
            universe, ["Fake"], price_data, _fake_get_strategy({"Fake": strategy})
        )
        tickers_in_order = [row["Ticker"] for row in results["Fake"]]
        self.assertEqual(tickers_in_order, ["WINNER_HIGH", "WINNER_LOW"])

    def test_min_price_filters_out_cheap_candidates(self):
        def analyze(ticker, data):
            price = float(data["Close"].iloc[-1])
            return {"Ticker": ticker, "Score": 1, "Signal": "BUY", "Price": price}

        strategy = _FakeStrategy(name="Fake", analyze=analyze, rank_key=lambda r: r["Score"])
        price_data = {"CHEAP": _price_frame([2.0]), "EXPENSIVE": _price_frame([50.0])}
        results = scan_for_candidates(
            ["CHEAP", "EXPENSIVE"],
            ["Fake"],
            price_data,
            _fake_get_strategy({"Fake": strategy}),
            min_price=5.0,
        )
        self.assertEqual([row["Ticker"] for row in results["Fake"]], ["EXPENSIVE"])

    def test_missing_price_data_is_skipped_without_raising(self):
        def analyze(ticker, data):
            return {"Ticker": ticker, "Score": 1, "Signal": "BUY", "Price": 10.0}

        strategy = _FakeStrategy(name="Fake", analyze=analyze, rank_key=lambda r: r["Score"])
        results = scan_for_candidates(
            ["NO_DATA"], ["Fake"], {}, _fake_get_strategy({"Fake": strategy})
        )
        self.assertEqual(results["Fake"], [])

    def test_analyze_exception_is_skipped_without_raising(self):
        def analyze(ticker, data):
            raise RuntimeError("boom")

        strategy = _FakeStrategy(name="Fake", analyze=analyze, rank_key=lambda r: r["Score"])
        results = scan_for_candidates(
            ["AAPL"],
            ["Fake"],
            {"AAPL": _price_frame([10.0])},
            _fake_get_strategy({"Fake": strategy}),
        )
        self.assertEqual(results["Fake"], [])

    def test_analyze_returning_none_is_skipped(self):
        def analyze(ticker, data):
            return None

        strategy = _FakeStrategy(name="Fake", analyze=analyze, rank_key=lambda r: r["Score"])
        results = scan_for_candidates(
            ["AAPL"],
            ["Fake"],
            {"AAPL": _price_frame([10.0])},
            _fake_get_strategy({"Fake": strategy}),
        )
        self.assertEqual(results["Fake"], [])


if __name__ == "__main__":
    unittest.main()
