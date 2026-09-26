"""Regression tests for the 2021-12-20 membership-date bug and related edges."""

import io
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

from backtesting.periods import generate_random_periods
from benchmarking.export import EXPORT_FILENAMES, build_benchmark_zip
from benchmarking.metrics import compute_aggregate_metrics
from benchmarking.ml_training import build_training_window
from benchmarking.promotion import INVALID, PROMOTE, evaluate_promotion
from benchmarking.runner import run_benchmark
from data.historical_universe import HISTORICAL_UNIVERSE_START, get_historical_universe
from ml.dataset import build_feature_dataset
from ml.evaluation import _classification_metrics, evaluate_top_n_picks
from ml.models import build_logistic_regression_pipeline
from tests.benchmark_fixtures import (
    BENCHMARK_TICKERS,
    TEST_TRAINING_WINDOW_DAYS,
    benchmark_period_bounds,
    build_benchmark_price_data,
)


def _period_row(test, total_return, benchmark_return=0.0):
    return {
        "Method": "M",
        "Test": test,
        "Requested Start": pd.Timestamp("2023-01-01").date(),
        "Requested End": pd.Timestamp("2023-02-01").date(),
        "Actual Start": pd.Timestamp("2023-01-01"),
        "Actual End": pd.Timestamp("2023-02-01"),
        "Total Return": total_return,
        "Benchmark Return": benchmark_return,
        "Excess Return": total_return - benchmark_return,
        "Number of Trades": 0,
        "Max Drawdown": 0.0,
        "Turnover": 0.0,
    }


class UniverseMembershipDateRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.price_data = build_benchmark_price_data()
        cls.universe = list(BENCHMARK_TICKERS)

    def test_2023_benchmark_never_looks_up_membership_before_universe_start(self):
        recorded = []
        original = get_historical_universe

        def _spy(date):
            recorded.append(pd.Timestamp(date).normalize())
            return original(date)

        with patch("data.historical_universe.get_historical_universe", side_effect=_spy), patch(
            "ml.dataset.get_historical_universe", side_effect=_spy
        ), patch("benchmarking.simulation.get_historical_universe", side_effect=_spy), patch(
            "benchmarking.runner.get_historical_universe", side_effect=_spy
        ), patch(
            "benchmarking.runner.download_backtest_data",
            return_value=(self.price_data, {}),
        ) as mock_download:
            run_benchmark(
                duration="1 month",
                earliest_allowed="2023-01-01",
                latest_allowed="2024-06-30",
                number_of_tests=2,
                random_seed=43,
                requested_strategies=("Baseline",),
                include_regime_switching=False,
                ml_models=("Logistic Regression",),
                top_n_values=(5,),
                universe=self.universe,
                price_data=None,
                training_window_days=TEST_TRAINING_WINDOW_DAYS,
            )

        download_start = pd.Timestamp(mock_download.call_args[0][0]).normalize()
        self.assertGreaterEqual(download_start, HISTORICAL_UNIVERSE_START)
        for date in recorded:
            self.assertGreaterEqual(
                date,
                HISTORICAL_UNIVERSE_START,
                msg=f"membership lookup at {date.date()} precedes {HISTORICAL_UNIVERSE_START.date()}",
            )

    def test_unsupported_evaluation_start_fails_early(self):
        with self.assertRaises(ValueError) as ctx:
            run_benchmark(
                duration="1 month",
                earliest_allowed="2019-01-01",
                latest_allowed="2020-06-01",
                number_of_tests=1,
                random_seed=1,
                requested_strategies=("Baseline",),
                include_regime_switching=False,
                ml_models=(),
                universe=None,
                price_data=self.price_data,
            )
        self.assertIn(str(HISTORICAL_UNIVERSE_START.date()), str(ctx.exception))

    def test_dataset_observation_before_universe_start_is_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            build_feature_dataset(
                "2019-01-01",
                "2019-06-01",
                universe=None,
                price_data=self.price_data,
            )
        self.assertIn(str(HISTORICAL_UNIVERSE_START.date()), str(ctx.exception))

    def test_warmup_price_history_can_precede_universe_start_with_explicit_universe(self):
        dataset = build_feature_dataset(
            "2022-06-01",
            "2022-08-01",
            universe=self.universe,
            rebalance_frequency="weekly",
            price_data=self.price_data,
        )
        self.assertFalse(dataset.empty)
        self.assertTrue((pd.to_datetime(dataset["Date"]) >= HISTORICAL_UNIVERSE_START).all())

    def test_valid_2022_2023_2024_evaluation_windows_are_accepted(self):
        for start, end in (
            ("2022-01-03", "2022-03-01"),
            ("2023-01-03", "2023-03-01"),
            ("2024-01-03", "2024-03-01"),
        ):
            train_start, train_end = build_training_window(
                start, HISTORICAL_UNIVERSE_START, TEST_TRAINING_WINDOW_DAYS
            )
            self.assertGreaterEqual(train_start, HISTORICAL_UNIVERSE_START)
            self.assertLess(train_end, pd.Timestamp(start))


class RandomPeriodTests(unittest.TestCase):
    def test_same_seed_produces_identical_periods(self):
        first = generate_random_periods("5 months", "2023-01-01", "2025-12-31", 5, 43)
        second = generate_random_periods("5 months", "2023-01-01", "2025-12-31", 5, 43)
        self.assertEqual(first, second)

    def test_periods_stay_inside_requested_bounds(self):
        earliest = pd.Timestamp("2023-01-01")
        latest = pd.Timestamp("2025-12-31")
        periods = generate_random_periods("5 months", earliest, latest, 8, 7)
        for start, end in periods:
            self.assertGreaterEqual(start, earliest)
            self.assertLessEqual(end, latest)
            self.assertLess(start, end)

    def test_narrow_range_fails_before_running(self):
        with self.assertRaises(ValueError):
            generate_random_periods("1 year", "2023-01-01", "2023-03-01", 2, 1)


class SamePeriodFairnessTests(unittest.TestCase):
    def test_all_methods_share_requested_period_bounds(self):
        price_data = build_benchmark_price_data()
        earliest, latest = benchmark_period_bounds(price_data)
        result = run_benchmark(
            duration="1 month",
            earliest_allowed=earliest,
            latest_allowed=latest,
            number_of_tests=2,
            random_seed=43,
            requested_strategies=("Baseline", "Momentum V2"),
            include_regime_switching=False,
            ml_models=("Logistic Regression",),
            top_n_values=(5,),
            universe=list(BENCHMARK_TICKERS),
            price_data=price_data,
            training_window_days=TEST_TRAINING_WINDOW_DAYS,
        )
        for _, group in result.period_table.groupby("Test"):
            self.assertEqual(group["Requested Start"].nunique(), 1)
            self.assertEqual(group["Requested End"].nunique(), 1)


class MetricEdgeCaseTests(unittest.TestCase):
    def test_zero_volatility_sharpe_is_nan(self):
        rows = [_period_row(i, 0.05) for i in range(6)]
        metrics = compute_aggregate_metrics(rows)
        self.assertTrue(pd.isna(metrics["Sharpe Ratio"]))

    def test_sortino_is_nan_when_there_is_no_downside(self):
        rows = [_period_row(i, 0.05 + i * 0.01) for i in range(6)]
        metrics = compute_aggregate_metrics(rows)
        self.assertTrue(pd.isna(metrics["Sortino Ratio"]))

    def test_exact_20_percent_still_counts(self):
        rows = [_period_row(1, 0.20)]
        metrics = compute_aggregate_metrics(rows)
        self.assertEqual(metrics["P(Return >= 20%)"], 1.0)


class EvaluationEdgeCaseTests(unittest.TestCase):
    def test_single_class_roc_auc_is_nan_not_crash(self):
        metrics = _classification_metrics(
            y_true=[True, True, True],
            y_pred=[True, True, False],
            y_proba=[0.9, 0.8, 0.7],
        )
        self.assertTrue(pd.isna(metrics["ROC AUC"]))

    def test_insufficient_top_n_does_not_crash(self):
        scored = pd.DataFrame(
            [
                {
                    "Date": "2023-01-06",
                    "Ticker": "A",
                    "Predicted Probability": 0.9,
                    "Forward Return 20D": 0.1,
                }
            ]
        )
        result = evaluate_top_n_picks(scored, n=20)
        self.assertEqual(result["Dates Evaluated"], 1)

    def test_unknown_regime_category_does_not_crash_predict(self):
        train = pd.DataFrame(
            {"num": [0.1, 0.2, 0.3, 0.4], "Regime": ["SIDEWAYS", "SIDEWAYS", "DOWNTREND", "DOWNTREND"]}
        )
        y = pd.Series([True, False, True, False])
        pipeline = build_logistic_regression_pipeline(["num"], ["Regime"])
        pipeline.fit(train, y)
        unseen = pd.DataFrame({"num": [0.15], "Regime": ["MADE_UP_REGIME"]})
        proba = pipeline.predict_proba(unseen)
        self.assertEqual(proba.shape[1], 2)
        self.assertTrue(np.isfinite(proba).all())


class ExportAndHygieneTests(unittest.TestCase):
    def test_zip_contains_expected_files_and_serializes_numpy(self):
        price_data = build_benchmark_price_data()
        earliest, latest = benchmark_period_bounds(price_data)
        result = run_benchmark(
            duration="1 month",
            earliest_allowed=earliest,
            latest_allowed=latest,
            number_of_tests=1,
            random_seed=43,
            requested_strategies=("Baseline",),
            include_regime_switching=False,
            ml_models=(),
            universe=list(BENCHMARK_TICKERS),
            price_data=price_data,
            training_window_days=TEST_TRAINING_WINDOW_DAYS,
        )
        result.config["numpy_float"] = np.float64(1.25)
        zip_bytes = build_benchmark_zip(result)
        self.assertGreater(len(zip_bytes), 0)
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
            self.assertEqual(set(archive.namelist()), set(EXPORT_FILENAMES))
            for name in EXPORT_FILENAMES:
                self.assertGreater(archive.getinfo(name).file_size, 0)

    def test_runtime_sqlite_is_gitignored(self):
        gitignore = Path(__file__).resolve().parents[1].joinpath(".gitignore").read_text(encoding="utf-8")
        self.assertIn("*.sqlite3", gitignore)
        self.assertIn("paper_trading_data/", gitignore)

    def test_invalid_leakage_cannot_promote(self):
        result = evaluate_promotion(
            "Logistic Regression Top 5",
            {
                "Median Excess Return": 0.1,
                "Beat SPY %": 0.9,
                "P(Return >= 20%)": 0.5,
                "Sharpe Ratio": 2.0,
                "Worst Period Return": -0.01,
                "Periods Tested": 20,
            },
            {
                "P(Return >= 20%)": 0.1,
                "Sharpe Ratio": 0.2,
                "Worst Period Return": -0.2,
            },
            "Baseline",
            [0.1] * 20,
            leakage_audit_is_valid=False,
        )
        self.assertEqual(result.decision, INVALID)
        self.assertNotEqual(result.decision, PROMOTE)


if __name__ == "__main__":
    unittest.main()
