"""Original vs optimized feature/prediction/trade equivalence."""

import unittest

import numpy as np
import pandas as pd

from backtesting.engine import rank_buy_candidates
from benchmarking.ml_training import fit_prepared_fold, prepare_training_fold
from benchmarking.simulation import MLRankingStrategy, run_portfolio_simulation
from config import BACKTEST_STARTING_CASH
from ml.dataset import build_feature_dataset
from ml.feature_cache import reset_feature_cache
from ml.features import REQUIRED_HISTORY_DAYS, compute_feature_row, truncate_to_as_of
from tests.benchmark_fixtures import (
    BENCHMARK_TICKERS,
    TEST_TRAINING_WINDOW_DAYS,
    benchmark_period_bounds,
    build_benchmark_price_data,
)
from tests.ml_fixtures import make_multi_asset_price_data

FEATURE_ATOL = 1e-10
PREDICTION_ATOL = 1e-12
RETURN_ATOL = 1e-10


def _assert_close_mapping(test, left, right, atol, label):
    test.assertEqual(set(left), set(right), label)
    for key in left:
        first, second = left[key], right[key]
        if isinstance(first, str) or isinstance(second, str):
            test.assertEqual(first, second, f"{label}:{key}")
            continue
        if pd.isna(first) and pd.isna(second):
            continue
        if isinstance(first, (bool, np.bool_)) or isinstance(second, (bool, np.bool_)):
            test.assertEqual(bool(first), bool(second), f"{label}:{key}")
            continue
        test.assertTrue(
            np.isclose(float(first), float(second), atol=atol, rtol=0.0, equal_nan=True),
            f"{label}:{key} {first} vs {second}",
        )


class FeatureEquivalenceTests(unittest.TestCase):
    def setUp(self):
        reset_feature_cache()
        self.price_data = make_multi_asset_price_data(
            n=REQUIRED_HISTORY_DAYS + 80, tickers=BENCHMARK_TICKERS, seed_offset=7
        )
        self.as_of = self.price_data["AAPL"].index[REQUIRED_HISTORY_DAYS + 30]

    def tearDown(self):
        reset_feature_cache()

    def test_cached_indicators_match_original_row_within_tolerance(self):
        historical = truncate_to_as_of(self.price_data["AAPL"], self.as_of)
        spy = truncate_to_as_of(self.price_data["SPY"], self.as_of)
        qqq = truncate_to_as_of(self.price_data["QQQ"], self.as_of)
        original = compute_feature_row(
            "AAPL", historical, spy, qqq, use_cache=False
        )
        reset_feature_cache()
        optimized = compute_feature_row(
            "AAPL",
            self.price_data["AAPL"],
            self.price_data["SPY"],
            self.price_data["QQQ"],
            as_of_date=self.as_of,
            use_cache=True,
        )
        _assert_close_mapping(self, original, optimized, FEATURE_ATOL, "feature_row")

    def test_future_perturbation_does_not_change_earlier_features(self):
        original = compute_feature_row(
            "AAPL",
            self.price_data["AAPL"],
            self.price_data["SPY"],
            self.price_data["QQQ"],
            as_of_date=self.as_of,
            use_cache=True,
        )
        shocked = {ticker: frame.copy() for ticker, frame in self.price_data.items()}
        for frame in shocked.values():
            future = frame.index > self.as_of
            frame.loc[future, "Close"] *= 4.0
            if "Volume" in frame.columns:
                frame.loc[future, "Volume"] *= 9.0
        reset_feature_cache()
        after_shock = compute_feature_row(
            "AAPL",
            shocked["AAPL"],
            shocked["SPY"],
            shocked["QQQ"],
            as_of_date=self.as_of,
            use_cache=True,
        )
        _assert_close_mapping(self, original, after_shock, FEATURE_ATOL, "future_shock")


class DatasetAndSimulationEquivalenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compact = make_multi_asset_price_data(
            n=REQUIRED_HISTORY_DAYS + 90, tickers=BENCHMARK_TICKERS, seed_offset=11
        )
        dates = compact["AAPL"].index
        cls.compact_prices = compact
        cls.feature_start = dates[REQUIRED_HISTORY_DAYS + 10]
        cls.feature_end = dates[REQUIRED_HISTORY_DAYS + 55]
        cls.price_data = build_benchmark_price_data()
        cls.period_start, _ = benchmark_period_bounds(cls.price_data)
        start_pos = cls.price_data["AAPL"].index.get_indexer([cls.period_start])[0]
        cls.period_end = cls.price_data["AAPL"].index[start_pos + 20]

    def tearDown(self):
        reset_feature_cache()

    def _dataset(self, use_cache):
        reset_feature_cache()
        from ml import dataset as dataset_module
        from ml import features as features_module

        original = features_module.compute_feature_row

        def patched(*args, **kwargs):
            kwargs["use_cache"] = use_cache
            return original(*args, **kwargs)

        features_module.compute_feature_row = patched
        dataset_module.compute_feature_row = patched
        try:
            return build_feature_dataset(
                self.feature_start,
                self.feature_end,
                universe=list(BENCHMARK_TICKERS),
                rebalance_frequency="weekly",
                price_data=self.compact_prices,
            )
        finally:
            features_module.compute_feature_row = original
            dataset_module.compute_feature_row = original

    def test_weekly_dataset_matches_uncached_features(self):
        cached = self._dataset(use_cache=True)
        uncached = self._dataset(use_cache=False)
        self.assertEqual(list(cached.columns), list(uncached.columns))
        self.assertEqual(len(cached), len(uncached))
        pd.testing.assert_series_equal(cached["Ticker"], uncached["Ticker"])
        pd.testing.assert_series_equal(cached["Date"], uncached["Date"])
        numeric = [
            column
            for column in cached.columns
            if column not in {"Ticker", "Date", "As Of Trading Date", "Regime"}
            and pd.api.types.is_numeric_dtype(cached[column])
        ]
        pd.testing.assert_frame_equal(
            cached[numeric].reset_index(drop=True),
            uncached[numeric].reset_index(drop=True),
            check_exact=False,
            atol=FEATURE_ATOL,
            rtol=0.0,
        )
        if "Regime" in cached.columns:
            pd.testing.assert_series_equal(cached["Regime"], uncached["Regime"])

    def test_predictions_trades_and_returns_match_and_folds_stay_isolated(self):
        reset_feature_cache()
        prepared = prepare_training_fold(
            self.period_start,
            self.price_data,
            universe=list(BENCHMARK_TICKERS),
            training_window_days=TEST_TRAINING_WINDOW_DAYS,
        )
        fold_one = fit_prepared_fold("Logistic Regression", prepared)
        fold_two = fit_prepared_fold("Logistic Regression", prepared)
        self.assertIsNot(fold_one.pipeline, fold_two.pipeline)
        self.assertIsNot(
            fold_one.pipeline.named_steps["model"],
            fold_two.pipeline.named_steps["model"],
        )

        benchmark_data = {"SPY": self.price_data["SPY"], "QQQ": self.price_data["QQQ"]}

        reset_feature_cache()
        batched_strategy = MLRankingStrategy(
            name="Logistic Regression",
            pipeline=fold_one.pipeline,
            feature_columns=fold_one.feature_columns,
            categorical_columns=fold_one.categorical_columns,
            benchmark_data=benchmark_data,
        )
        batched = run_portfolio_simulation(
            batched_strategy,
            self.period_start,
            self.period_end,
            self.price_data,
            starting_cash=BACKTEST_STARTING_CASH,
            top_n=2,
            universe=list(BENCHMARK_TICKERS),
        )
        reset_feature_cache()
        per_row_strategy = MLRankingStrategy(
            name="Logistic Regression",
            pipeline=fold_one.pipeline,
            feature_columns=fold_one.feature_columns,
            categorical_columns=fold_one.categorical_columns,
            benchmark_data=benchmark_data,
        )
        per_row_strategy.rank_universe = None
        per_row = run_portfolio_simulation(
            per_row_strategy,
            self.period_start,
            self.period_end,
            self.price_data,
            starting_cash=BACKTEST_STARTING_CASH,
            top_n=2,
            universe=list(BENCHMARK_TICKERS),
        )

        self.assertEqual(batched["Trades"], per_row["Trades"])
        self.assertTrue(
            np.isclose(
                batched["Total Return"],
                per_row["Total Return"],
                atol=RETURN_ATOL,
                rtol=0.0,
            )
        )
        self.assertEqual(
            [row["Date"] for row in batched["Portfolio History"]],
            [row["Date"] for row in per_row["Portfolio History"]],
        )
        self.assertTrue(
            np.allclose(
                [row["Portfolio Value"] for row in batched["Portfolio History"]],
                [row["Portfolio Value"] for row in per_row["Portfolio History"]],
                atol=RETURN_ATOL,
                rtol=0.0,
            )
        )

        batched_probs = pd.DataFrame(batched_strategy.probability_log)
        per_row_probs = pd.DataFrame(per_row_strategy.probability_log)
        if not batched_probs.empty:
            merged = batched_probs.merge(
                per_row_probs,
                on=["Date", "Ticker"],
                suffixes=("_batched", "_row"),
            )
            self.assertGreater(len(merged), 0)
            self.assertTrue(
                np.allclose(
                    merged["Probability_batched"],
                    merged["Probability_row"],
                    atol=PREDICTION_ATOL,
                    rtol=0.0,
                    equal_nan=True,
                )
            )

        ranked_batched = rank_buy_candidates(
            self.price_data,
            self.period_start,
            universe=list(BENCHMARK_TICKERS),
            strategy=MLRankingStrategy(
                name="Logistic Regression",
                pipeline=fold_one.pipeline,
                feature_columns=fold_one.feature_columns,
                categorical_columns=fold_one.categorical_columns,
                benchmark_data=benchmark_data,
            ),
        )
        per_stock = MLRankingStrategy(
            name="Logistic Regression",
            pipeline=fold_one.pipeline,
            feature_columns=fold_one.feature_columns,
            categorical_columns=fold_one.categorical_columns,
            benchmark_data=benchmark_data,
        )
        per_stock.rank_universe = None
        ranked_each = rank_buy_candidates(
            self.price_data,
            self.period_start,
            universe=list(BENCHMARK_TICKERS),
            strategy=per_stock,
        )
        self.assertEqual(
            [item["Ticker"] for item in ranked_batched],
            [item["Ticker"] for item in ranked_each],
        )
        self.assertTrue(
            np.allclose(
                [item["Score"] for item in ranked_batched],
                [item["Score"] for item in ranked_each],
                atol=PREDICTION_ATOL,
                rtol=0.0,
            )
        )
