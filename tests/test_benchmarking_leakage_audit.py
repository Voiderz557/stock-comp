"""Tests for `benchmarking.leakage_audit`."""

import dataclasses
import unittest

import pandas as pd

from tests.benchmark_fixtures import (
    BENCHMARK_TICKERS,
    TEST_TRAINING_WINDOW_DAYS,
    benchmark_period_bounds,
    build_benchmark_price_data,
)

from benchmarking.leakage_audit import run_leakage_audit
from benchmarking.ml_training import train_ml_model_for_period


class LeakageAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.price_data = build_benchmark_price_data()
        earliest, _ = benchmark_period_bounds(cls.price_data)
        dates = cls.price_data["AAPL"].index
        earliest_position = dates.get_indexer([earliest])[0]
        second_period_start = dates[earliest_position + 30]

        cls.folds = [
            train_ml_model_for_period(
                "Logistic Regression",
                period_start,
                cls.price_data,
                universe=list(BENCHMARK_TICKERS),
                training_window_days=TEST_TRAINING_WINDOW_DAYS,
            )
            for period_start in (earliest, second_period_start)
        ]
        cls.universe = list(BENCHMARK_TICKERS)

    def test_valid_folds_pass_every_check(self):
        report = run_leakage_audit(
            self.folds, self.price_data, self.universe, self.folds[0].feature_columns
        )
        self.assertTrue(report["Is Valid"])
        for check in report["Checks"]:
            self.assertTrue(check["Passed"], msg=f"{check['Check']} failed: {check['Detail']}")

    def test_all_required_checks_are_present(self):
        report = run_leakage_audit(
            self.folds, self.price_data, self.universe, self.folds[0].feature_columns
        )
        check_names = {check["Check"] for check in report["Checks"]}
        expected = {
            "Feature timestamp <= prediction timestamp",
            "Training dates strictly before validation/period dates",
            "Label horizon never enters training features",
            "Future rows cannot alter earlier predictions",
            "Scaler/preprocessor fitted on training data only",
            "Model fitted on training data only",
            "No overlap between train and validation windows",
        }
        self.assertEqual(check_names, expected)

    def test_training_after_period_start_is_flagged_invalid(self):
        # Tamper with one fold so its "training" window actually extends
        # past its own period_start - a direct leakage violation.
        tampered_fold = dataclasses.replace(
            self.folds[0], training_end=self.folds[0].period_start + pd.Timedelta(days=5)
        )
        report = run_leakage_audit(
            [tampered_fold], self.price_data, self.universe, tampered_fold.feature_columns
        )
        self.assertFalse(report["Is Valid"])
        failing_checks = {check["Check"] for check in report["Checks"] if not check["Passed"]}
        self.assertIn("Training dates strictly before validation/period dates", failing_checks)

    def test_label_leakage_into_features_is_detected(self):
        report = run_leakage_audit(
            self.folds,
            self.price_data,
            self.universe,
            self.folds[0].feature_columns + ["beats_SPY_20D"],
        )
        self.assertFalse(report["Is Valid"])
        failing_checks = {check["Check"] for check in report["Checks"] if not check["Passed"]}
        self.assertIn("Label horizon never enters training features", failing_checks)

    def test_no_folds_fails_closed_instead_of_passing(self):
        report = run_leakage_audit([], self.price_data, self.universe, [])
        self.assertIn("Is Valid", report)
        self.assertFalse(report["Is Valid"])


if __name__ == "__main__":
    unittest.main()
