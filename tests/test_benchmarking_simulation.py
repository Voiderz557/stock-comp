"""Tests for `benchmarking.simulation`."""

import unittest

from tests.benchmark_fixtures import (
    BENCHMARK_TICKERS,
    TEST_TRAINING_WINDOW_DAYS,
    benchmark_period_bounds,
    build_benchmark_price_data,
)

from benchmarking.ml_training import train_ml_model_for_period
from benchmarking.simulation import (
    MLRankingStrategy,
    RegimeSwitchingStrategy,
    run_portfolio_simulation,
)
from config import MAX_POSITION_VALUE
from strategies.registry import available_strategy_names, get_strategy, invoke_analyze


class MLRankingStrategyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.price_data = build_benchmark_price_data()
        earliest, cls.latest_allowed = benchmark_period_bounds(cls.price_data)
        cls.period_start = earliest
        cls.benchmark_data = {"SPY": cls.price_data["SPY"], "QQQ": cls.price_data["QQQ"]}
        cls.fold = train_ml_model_for_period(
            "Logistic Regression",
            cls.period_start,
            cls.price_data,
            universe=list(BENCHMARK_TICKERS),
            training_window_days=TEST_TRAINING_WINDOW_DAYS,
        )

    def _strategy(self):
        return MLRankingStrategy(
            name="Logistic Regression",
            pipeline=self.fold.pipeline,
            feature_columns=self.fold.feature_columns,
            categorical_columns=self.fold.categorical_columns,
            benchmark_data=self.benchmark_data,
        )

    def test_analyze_is_deterministic(self):
        ticker = BENCHMARK_TICKERS[0]
        historical = self.price_data[ticker].loc[self.price_data[ticker].index < self.period_start]

        strategy_one = self._strategy()
        strategy_two = self._strategy()
        result_one = strategy_one.analyze(ticker, historical)
        result_two = strategy_two.analyze(ticker, historical)

        self.assertIsNotNone(result_one)
        self.assertEqual(result_one["Score"], result_two["Score"])
        self.assertEqual(result_one["Signal"], "BUY")

    def test_ranking_order_is_deterministic_and_sorted_descending(self):
        strategy = self._strategy()
        candidates = []
        for ticker in BENCHMARK_TICKERS:
            historical = self.price_data[ticker].loc[self.price_data[ticker].index < self.period_start]
            result = strategy.analyze(ticker, historical)
            if result is not None:
                candidates.append(result)
        candidates.sort(key=strategy.rank_key, reverse=True)
        scores = [candidate["Score"] for candidate in candidates]
        self.assertEqual(scores, sorted(scores, reverse=True))

        # Re-running with a fresh strategy instance (same fitted pipeline)
        # must reproduce the exact same ranking order.
        strategy_repeat = self._strategy()
        repeat_candidates = []
        for ticker in BENCHMARK_TICKERS:
            historical = self.price_data[ticker].loc[self.price_data[ticker].index < self.period_start]
            result = strategy_repeat.analyze(ticker, historical)
            if result is not None:
                repeat_candidates.append(result)
        repeat_candidates.sort(key=strategy_repeat.rank_key, reverse=True)
        self.assertEqual(
            [candidate["Ticker"] for candidate in candidates],
            [candidate["Ticker"] for candidate in repeat_candidates],
        )


class RegimeSwitchingStrategyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.price_data = build_benchmark_price_data()

    def test_delegates_to_an_available_strategy(self):
        benchmark_data = {"SPY": self.price_data["SPY"], "QQQ": self.price_data["QQQ"]}
        strategy = RegimeSwitchingStrategy(benchmark_data)
        ticker = BENCHMARK_TICKERS[0]
        as_of_index = 700
        historical = self.price_data[ticker].iloc[:as_of_index]

        result = strategy.analyze(ticker, historical)

        self.assertGreaterEqual(len(strategy.regime_log), 1)
        chosen_name = strategy.regime_log[-1]["Chosen Strategy"]
        self.assertIn(
            chosen_name,
            available_strategy_names(),
        )
        if result is not None:
            delegate = get_strategy(chosen_name)
            preferred = getattr(delegate, "benchmark_ticker", None)
            relative = benchmark_data.get(preferred) if preferred else None
            if relative is not None:
                relative = relative.loc[relative.index <= historical.index[-1]]
            delegate_result = invoke_analyze(
                delegate.analyze, ticker, historical, benchmark_data=relative
            )
            self.assertEqual(result["Signal"], delegate_result["Signal"])

    def test_rank_key_matches_the_chosen_delegate_for_that_date(self):
        benchmark_data = {"SPY": self.price_data["SPY"], "QQQ": self.price_data["QQQ"]}
        strategy = RegimeSwitchingStrategy(benchmark_data)
        as_of_index = 900
        candidates = []
        for ticker in BENCHMARK_TICKERS:
            historical = self.price_data[ticker].iloc[:as_of_index]
            result = strategy.analyze(ticker, historical)
            if result is not None and result["Signal"] == "BUY":
                candidates.append(result)
        chosen_name = strategy.regime_log[-1]["Chosen Strategy"]
        for candidate in candidates:
            self.assertEqual(strategy.rank_key(candidate), get_strategy(chosen_name).rank_key(candidate))


class RunPortfolioSimulationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.price_data = build_benchmark_price_data()
        cls.earliest_allowed, cls.latest_allowed = benchmark_period_bounds(cls.price_data)

    def test_portfolio_cap_is_respected_on_every_buy_trade(self):
        strategy = get_strategy("Baseline")
        period_end = self.price_data["AAPL"].index[
            self.price_data["AAPL"].index.get_indexer([self.earliest_allowed])[0] + 25
        ]
        result = run_portfolio_simulation(
            strategy,
            self.earliest_allowed,
            period_end,
            self.price_data,
            universe=list(BENCHMARK_TICKERS),
        )
        buy_trades = [trade for trade in result["Trades"] if trade["Action"] == "BUY"]
        self.assertGreater(len(buy_trades), 0)
        for trade in buy_trades:
            self.assertLessEqual(trade["Shares"] * trade["Price"], MAX_POSITION_VALUE + 1e-6)

    def test_cash_is_allowed_when_top_n_exceeds_available_candidates(self):
        earliest, _ = benchmark_period_bounds(self.price_data)
        fold = train_ml_model_for_period(
            "Logistic Regression",
            earliest,
            self.price_data,
            universe=list(BENCHMARK_TICKERS),
            training_window_days=TEST_TRAINING_WINDOW_DAYS,
        )
        strategy = MLRankingStrategy(
            name="Logistic Regression",
            pipeline=fold.pipeline,
            feature_columns=fold.feature_columns,
            categorical_columns=fold.categorical_columns,
            benchmark_data={"SPY": self.price_data["SPY"], "QQQ": self.price_data["QQQ"]},
        )
        period_end = self.price_data["AAPL"].index[
            self.price_data["AAPL"].index.get_indexer([earliest])[0] + 25
        ]
        # top_n=20 but only 4 tickers ever have computable features/data -
        # the portfolio can never fully deploy $100,000 into just 4 x $20,000
        # positions, so meaningful cash must remain.
        result = run_portfolio_simulation(
            strategy,
            earliest,
            period_end,
            self.price_data,
            top_n=20,
            universe=list(BENCHMARK_TICKERS),
        )
        final_cash = result["Portfolio History"][-1]["Cash"]
        self.assertGreater(final_cash, 0)

    def test_top_n_holds_at_most_n_positions(self):
        earliest, _ = benchmark_period_bounds(self.price_data)
        fold = train_ml_model_for_period(
            "Logistic Regression",
            earliest,
            self.price_data,
            universe=list(BENCHMARK_TICKERS),
            training_window_days=TEST_TRAINING_WINDOW_DAYS,
        )
        strategy = MLRankingStrategy(
            name="Logistic Regression",
            pipeline=fold.pipeline,
            feature_columns=fold.feature_columns,
            categorical_columns=fold.categorical_columns,
            benchmark_data={"SPY": self.price_data["SPY"], "QQQ": self.price_data["QQQ"]},
        )
        period_end = self.price_data["AAPL"].index[
            self.price_data["AAPL"].index.get_indexer([earliest])[0] + 25
        ]
        result = run_portfolio_simulation(
            strategy,
            earliest,
            period_end,
            self.price_data,
            top_n=2,
            universe=list(BENCHMARK_TICKERS),
        )
        self.assertLessEqual(len(result["Holdings"]), 2)


if __name__ == "__main__":
    unittest.main()
