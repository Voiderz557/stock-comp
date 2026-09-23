import unittest
from types import SimpleNamespace

import pandas as pd

from config import MAX_POSITION_VALUE, MIN_STOCK_PRICE
from market.regime import DOWNTREND, SIDEWAYS
from paper_trading.prices import SOURCE_LABEL
from paper_trading.trading_plan import (
    AUTOMATIC_MODE,
    KEEP,
    REVIEW_EXIT,
    UNAVAILABLE,
    allocate_buy_candidates,
    build_trading_plan,
    plan_to_csv,
    resolve_plan_strategy,
    review_holdings,
)


def _frame(closes, start="2025-01-02"):
    return pd.DataFrame(
        {"Close": closes, "Open": closes, "Volume": [1_000] * len(closes)},
        index=pd.bdate_range(start, periods=len(closes)),
    )


def _quote_frame(price, as_of="2025-06-02"):
    return _frame([price], start=as_of)


def _account(cash=100_000.0, starting=100_000.0):
    return {"Cash": cash, "Starting Capital": starting, "Realized P&L": 0.0}


def _long(ticker, quantity, price, position_id=1):
    return {
        "id": position_id,
        "ticker": ticker,
        "direction": "LONG",
        "quantity": quantity,
        "entry_price": price,
        "allocated_capital": quantity * price,
        "strategy": "Baseline",
        "reason": "test",
    }


def _strategy(signals, scores=None):
    scores = scores or {}

    def analyze(ticker, data, benchmark_data=None):
        signal = signals.get(ticker)
        if signal is None:
            return None
        close = float(data["Close"].iloc[-1])
        return {
            "Ticker": ticker,
            "Signal": signal,
            "Score": scores.get(ticker, 1.0),
            "Reason": f"{ticker} is {signal}",
            "Price": close,
        }

    return SimpleNamespace(
        name="Test Strategy",
        analyze=analyze,
        rank_key=lambda result: result["Score"],
        required_history_days=1,
        benchmark_ticker=None,
    )


class ResolveStrategyTests(unittest.TestCase):
    def test_automatic_uses_first_available_preferred_strategy(self):
        available = {"Mean Reversion V1": _strategy({})}

        def getter(name):
            if name not in available:
                raise ValueError(name)
            return available[name]

        resolved = resolve_plan_strategy(
            AUTOMATIC_MODE,
            SimpleNamespace(regime=SIDEWAYS),
            get_strategy_fn=getter,
        )
        self.assertEqual(resolved["strategy_name"], "Mean Reversion V1")
        self.assertIn("range-bound", resolved["strategy_reason"])

    def test_automatic_does_not_invent_a_strategy_in_downtrend(self):
        resolved = resolve_plan_strategy(
            AUTOMATIC_MODE,
            SimpleNamespace(regime=DOWNTREND),
            get_strategy_fn=lambda name: (_ for _ in ()).throw(ValueError(name)),
        )
        self.assertIsNone(resolved["strategy_name"])
        self.assertIsNone(resolved["definition"])


class AllocationTests(unittest.TestCase):
    def test_allocates_sequentially_across_multiple_candidates(self):
        ranked = [
            {"Ticker": "AAA", "Score": 3, "Reason": "strong"},
            {"Ticker": "BBB", "Score": 2, "Reason": "next"},
            {"Ticker": "CCC", "Score": 1, "Reason": "last"},
        ]
        quotes = {
            "AAA": {"price": 100.0, "as_of": "2025-06-02", "source_label": SOURCE_LABEL},
            "BBB": {"price": 50.0, "as_of": "2025-06-02", "source_label": SOURCE_LABEL},
            "CCC": {"price": 25.0, "as_of": "2025-06-02", "source_label": SOURCE_LABEL},
        }
        result = allocate_buy_candidates(
            ranked, _account(cash=50_000.0), [], quotes
        )
        self.assertEqual([row["Ticker"] for row in result["buys"]], ["AAA", "BBB", "CCC"])
        self.assertAlmostEqual(result["buys"][0]["Allocation"], MAX_POSITION_VALUE)
        self.assertAlmostEqual(result["buys"][0]["Quantity"], MAX_POSITION_VALUE / 100.0)
        self.assertAlmostEqual(result["buys"][1]["Allocation"], MAX_POSITION_VALUE)
        self.assertAlmostEqual(result["buys"][2]["Allocation"], 10_000.0)
        self.assertAlmostEqual(result["cash_after"], 0.0)

    def test_existing_same_ticker_long_reduces_room_under_cap(self):
        ranked = [{"Ticker": "AAPL", "Score": 1, "Reason": "buy"}]
        quotes = {
            "AAPL": {"price": 100.0, "as_of": "2025-06-02", "source_label": SOURCE_LABEL}
        }
        holdings = [_long("AAPL", 150.0, 80.0)]
        result = allocate_buy_candidates(
            ranked, _account(), holdings, quotes
        )
        self.assertEqual(len(result["buys"]), 1)
        self.assertAlmostEqual(result["buys"][0]["Allocation"], 5_000.0)
        self.assertAlmostEqual(result["buys"][0]["Quantity"], 50.0)

    def test_exhausted_capacity_blocks_all_buys(self):
        ranked = [{"Ticker": "AAA", "Score": 1, "Reason": "buy"}]
        quotes = {
            "AAA": {"price": 10.0, "as_of": "2025-06-02", "source_label": SOURCE_LABEL},
            "MSFT": {"price": 100.0, "as_of": "2025-06-02", "source_label": SOURCE_LABEL},
        }
        holdings = [_long("MSFT", 1_000.0, 100.0)]
        result = allocate_buy_candidates(
            ranked, _account(cash=100_000.0, starting=100_000.0), holdings, quotes
        )
        self.assertEqual(result["buys"], [])
        self.assertTrue(
            any("capacity" in item["Reason"].lower() for item in result["skipped"])
        )
        self.assertAlmostEqual(result["cash_after"], 100_000.0)

    def test_missing_candidate_price_is_not_allocated(self):
        ranked = [
            {"Ticker": "AAA", "Score": 2, "Reason": "buy"},
            {"Ticker": "BBB", "Score": 1, "Reason": "buy"},
        ]
        quotes = {
            "BBB": {"price": 20.0, "as_of": "2025-06-02", "source_label": SOURCE_LABEL}
        }
        result = allocate_buy_candidates(ranked, _account(), [], quotes)
        self.assertEqual([row["Ticker"] for row in result["buys"]], ["BBB"])
        self.assertEqual(result["skipped"][0]["Ticker"], "AAA")
        self.assertIn("unavailable", result["skipped"][0]["Reason"].lower())

    def test_below_minimum_price_is_not_allocated(self):
        ranked = [{"Ticker": "PENNY", "Score": 1, "Reason": "buy"}]
        quotes = {
            "PENNY": {
                "price": MIN_STOCK_PRICE - 0.5,
                "as_of": "2025-06-02",
                "source_label": SOURCE_LABEL,
            }
        }
        result = allocate_buy_candidates(ranked, _account(), [], quotes)
        self.assertEqual(result["buys"], [])
        self.assertIn("minimum", result["skipped"][0]["Reason"].lower())


class HoldingsReviewTests(unittest.TestCase):
    def test_buy_and_wait_are_keep_avoid_is_review_exit(self):
        definition = _strategy({"AAPL": "BUY", "MSFT": "WAIT", "NVDA": "AVOID"})
        prices = {
            "AAPL": _quote_frame(100.0),
            "MSFT": _quote_frame(50.0),
            "NVDA": _quote_frame(80.0),
        }
        reviews = review_holdings(
            [_long("AAPL", 1, 90, 1), _long("MSFT", 1, 50, 2), _long("NVDA", 1, 80, 3)],
            definition,
            prices,
        )
        actions = {row["ticker"]: (row["Action"], row["Signal"]) for row in reviews}
        self.assertEqual(actions["AAPL"], (KEEP, "BUY"))
        self.assertEqual(actions["MSFT"], (KEEP, "WAIT"))
        self.assertEqual(actions["NVDA"], (REVIEW_EXIT, "AVOID"))

    def test_missing_data_is_unavailable_and_does_not_infer_exit(self):
        definition = _strategy({"AAPL": "BUY"})
        reviews = review_holdings(
            [_long("MSFT", 1, 50.0)],
            definition,
            {"AAPL": _quote_frame(100.0)},
        )
        self.assertEqual(reviews[0]["Action"], UNAVAILABLE)
        self.assertIn("not inferred", reviews[0]["Reason"].lower())


class BuildPlanTests(unittest.TestCase):
    def test_no_qualifying_buys_explains_empty_plan(self):
        definition = _strategy({"AAPL": "WAIT"})
        plan = build_trading_plan(
            "Test Strategy",
            _account(),
            [],
            {"AAPL": _quote_frame(100.0)},
            ["AAPL"],
            get_strategy_fn=lambda name: definition,
        )
        self.assertEqual(plan["buys"], [])
        self.assertIn("No purchases qualify", plan["no_purchase_explanation"])
        self.assertAlmostEqual(plan["cash_after"], 100_000.0)

    def test_proposed_exits_do_not_free_cash_or_capacity(self):
        definition = _strategy({"AAPL": "AVOID", "MSFT": "BUY"}, scores={"MSFT": 2})
        prices = {
            "AAPL": _quote_frame(100.0),
            "MSFT": _quote_frame(50.0),
        }
        holdings = [_long("AAPL", 1_000.0, 100.0)]
        plan = build_trading_plan(
            "Test Strategy",
            _account(cash=10_000.0, starting=100_000.0),
            holdings,
            prices,
            ["AAPL", "MSFT"],
            get_strategy_fn=lambda name: definition,
        )
        self.assertEqual(plan["holdings"][0]["Action"], REVIEW_EXIT)
        self.assertEqual(plan["buys"], [])
        self.assertAlmostEqual(plan["cash_after"], 10_000.0)
        self.assertIn("capacity", (plan["no_purchase_explanation"] or "").lower())

    def test_automatic_downtrend_has_no_purchases_and_unavailable_holdings(self):
        plan = build_trading_plan(
            AUTOMATIC_MODE,
            _account(),
            [_long("AAPL", 1, 100.0)],
            {"AAPL": _quote_frame(100.0)},
            ["AAPL"],
            regime_result=SimpleNamespace(regime=DOWNTREND),
            get_strategy_fn=lambda name: (_ for _ in ()).throw(ValueError(name)),
        )
        self.assertIsNone(plan["strategy_name"])
        self.assertEqual(plan["buys"], [])
        self.assertIn("No purchases qualify", plan["no_purchase_explanation"])
        self.assertEqual(plan["holdings"][0]["Action"], UNAVAILABLE)

    def test_csv_includes_buys_holdings_and_cash(self):
        definition = _strategy({"AAPL": "BUY", "MSFT": "AVOID"}, scores={"AAPL": 5})
        prices = {
            "AAPL": _quote_frame(25.0),
            "MSFT": _quote_frame(40.0),
        }
        plan = build_trading_plan(
            "Test Strategy",
            _account(),
            [_long("MSFT", 10, 40.0)],
            prices,
            ["AAPL", "MSFT"],
            get_strategy_fn=lambda name: definition,
        )
        csv_text = plan_to_csv(plan)
        self.assertIn("Proposed BUY", csv_text)
        self.assertIn("AAPL", csv_text)
        self.assertIn("REVIEW EXIT", csv_text)
        self.assertIn("Cash after proposed buys", csv_text)
        self.assertIn(SOURCE_LABEL, plan["price_source"])
