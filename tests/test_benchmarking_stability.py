"""Tests for `benchmarking.stability`."""

import unittest

from tests.benchmark_fixtures import (
    BENCHMARK_TICKERS,
    TEST_TRAINING_WINDOW_DAYS,
    benchmark_period_bounds,
    build_benchmark_price_data,
)

from benchmarking.stability import (
    build_fold_metrics_table,
    compute_coefficient_sign_consistency,
    compute_feature_stability,
    compute_importance_rank_consistency,
    compute_prediction_distribution,
)
from benchmarking.ml_training import train_ml_model_for_period


class StabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.price_data = build_benchmark_price_data()
        earliest, _ = benchmark_period_bounds(cls.price_data)
        dates = cls.price_data["AAPL"].index
        earliest_position = dates.get_indexer([earliest])[0]
        period_starts = [dates[earliest_position + offset] for offset in (0, 30, 60)]

        cls.logreg_folds = [
            train_ml_model_for_period(
                "Logistic Regression",
                period_start,
                cls.price_data,
                universe=list(BENCHMARK_TICKERS),
                training_window_days=TEST_TRAINING_WINDOW_DAYS,
            )
            for period_start in period_starts
        ]
        cls.rf_folds = [
            train_ml_model_for_period(
                "Random Forest",
                period_start,
                cls.price_data,
                universe=list(BENCHMARK_TICKERS),
                training_window_days=TEST_TRAINING_WINDOW_DAYS,
            )
            for period_start in period_starts
        ]

    def test_fold_metrics_table_has_one_row_per_fold(self):
        table = build_fold_metrics_table(self.logreg_folds + self.rf_folds)
        self.assertEqual(len(table), len(self.logreg_folds) + len(self.rf_folds))
        self.assertIn("Class Balance True %", table.columns)
        self.assertIn("Training Rows", table.columns)

    def test_feature_stability_summary_has_similarity_score(self):
        summary, detail = compute_feature_stability("Logistic Regression", self.logreg_folds)
        self.assertEqual(summary["Folds"], len(self.logreg_folds))
        self.assertFalse(detail.empty)
        self.assertIn("Average Top-Feature Jaccard Similarity", summary)
        self.assertGreaterEqual(summary["Average Top-Feature Jaccard Similarity"], 0.0)
        self.assertLessEqual(summary["Average Top-Feature Jaccard Similarity"], 1.0)

    def test_identical_folds_are_perfectly_stable(self):
        # Three folds that are literally the same trained pipeline object
        # must show maximal (1.0) top-feature similarity - a "control"
        # case proving the stability metric behaves sensibly.
        summary, _ = compute_feature_stability(
            "Logistic Regression", [self.logreg_folds[0]] * 3
        )
        self.assertAlmostEqual(summary["Average Top-Feature Jaccard Similarity"], 1.0)
        self.assertFalse(summary["Unstable Feature Importance"])

    def test_coefficient_sign_consistency_for_logistic_regression(self):
        _, detail = compute_feature_stability("Logistic Regression", self.logreg_folds)
        consistency_table = compute_coefficient_sign_consistency(detail)
        self.assertIn("Sign Consistency", consistency_table.columns)
        self.assertTrue((consistency_table["Sign Consistency"] >= 0).all())
        self.assertTrue((consistency_table["Sign Consistency"] <= 1).all())

    def test_importance_rank_consistency_for_random_forest(self):
        _, detail = compute_feature_stability("Random Forest", self.rf_folds)
        rank_table = compute_importance_rank_consistency(detail)
        self.assertIn("Mean Rank", rank_table.columns)
        self.assertFalse(rank_table.empty)

    def test_prediction_distribution_summarizes_probabilities(self):
        records = [{"Probability": value} for value in (0.1, 0.5, 0.9)]
        distribution = compute_prediction_distribution(records)
        self.assertEqual(distribution["Predictions"], 3)
        self.assertAlmostEqual(distribution["Mean Probability"], 0.5)
        self.assertEqual(distribution["Min Probability"], 0.1)
        self.assertEqual(distribution["Max Probability"], 0.9)

    def test_prediction_distribution_handles_empty_input(self):
        distribution = compute_prediction_distribution([])
        self.assertEqual(distribution["Predictions"], 0)


if __name__ == "__main__":
    unittest.main()
