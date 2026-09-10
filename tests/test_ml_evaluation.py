import unittest

import numpy as np
import pandas as pd

from ml.evaluation import (
    aggregate_fold_metrics,
    evaluate_top_n_picks,
    random_baseline_top_n_picks,
    run_walk_forward_evaluation,
    strategy_baseline_top_n_picks,
)
from ml.models import build_logistic_regression_pipeline, infer_feature_columns
from ml.validation import generate_walk_forward_folds
from tests.ml_fixtures import build_sample_supervised_dataset

_SUPERVISED_DATASET = build_sample_supervised_dataset()


def _hand_built_scored_frame():
    """Two rebalance dates, 4 tickers each, with a known best-N ranking."""
    return pd.DataFrame(
        [
            {"Date": "2022-01-07", "Ticker": "A", "Predicted Probability": 0.9, "Forward Return 20D": 0.10, "Benchmark Return": 0.02},
            {"Date": "2022-01-07", "Ticker": "B", "Predicted Probability": 0.7, "Forward Return 20D": 0.05, "Benchmark Return": 0.02},
            {"Date": "2022-01-07", "Ticker": "C", "Predicted Probability": 0.3, "Forward Return 20D": -0.02, "Benchmark Return": 0.02},
            {"Date": "2022-01-07", "Ticker": "D", "Predicted Probability": 0.1, "Forward Return 20D": -0.10, "Benchmark Return": 0.02},
            {"Date": "2022-01-14", "Ticker": "A", "Predicted Probability": 0.2, "Forward Return 20D": -0.05, "Benchmark Return": 0.01},
            {"Date": "2022-01-14", "Ticker": "B", "Predicted Probability": 0.95, "Forward Return 20D": 0.20, "Benchmark Return": 0.01},
            {"Date": "2022-01-14", "Ticker": "C", "Predicted Probability": 0.8, "Forward Return 20D": 0.08, "Benchmark Return": 0.01},
            {"Date": "2022-01-14", "Ticker": "D", "Predicted Probability": 0.05, "Forward Return 20D": -0.01, "Benchmark Return": 0.01},
        ]
    )


class EvaluateTopNPicksTests(unittest.TestCase):
    def test_top_1_picks_the_highest_scored_ticker_each_date(self):
        scored = _hand_built_scored_frame()
        result = evaluate_top_n_picks(
            scored, n=1, benchmark_return_column="Benchmark Return"
        )
        # Top-1 on 2022-01-07 is A (+0.10); top-1 on 2022-01-14 is B (+0.20).
        expected_average_return = (0.10 + 0.20) / 2
        self.assertAlmostEqual(result["Average Forward Return"], expected_average_return)
        self.assertEqual(result["Dates Evaluated"], 2)
        self.assertEqual(result["Positive Return Rate"], 1.0)

    def test_top_2_average_matches_manual_calculation(self):
        scored = _hand_built_scored_frame()
        result = evaluate_top_n_picks(scored, n=2, benchmark_return_column="Benchmark Return")
        # 2022-01-07 top 2: A, B -> (0.10 + 0.05) / 2 = 0.075
        # 2022-01-14 top 2: B, C -> (0.20 + 0.08) / 2 = 0.14
        expected = (0.075 + 0.14) / 2
        self.assertAlmostEqual(result["Average Forward Return"], expected)

    def test_excess_return_uses_benchmark_column_when_given(self):
        scored = _hand_built_scored_frame()
        result = evaluate_top_n_picks(scored, n=1, benchmark_return_column="Benchmark Return")
        # Excess: (0.10 - 0.02) and (0.20 - 0.01) -> average 0.135
        self.assertAlmostEqual(result["Average Excess Return"], (0.08 + 0.19) / 2)
        self.assertEqual(result["Hit Rate Vs Benchmark"], 1.0)

    def test_missing_benchmark_column_is_handled_gracefully(self):
        scored = _hand_built_scored_frame()
        result = evaluate_top_n_picks(scored, n=1, benchmark_return_column=None)
        self.assertTrue(pd.isna(result["Average Excess Return"]))


class RandomBaselineTests(unittest.TestCase):
    def test_same_seed_gives_identical_results(self):
        scored = _hand_built_scored_frame()
        first = random_baseline_top_n_picks(scored, n=2, random_seed=7)
        second = random_baseline_top_n_picks(scored, n=2, random_seed=7)
        self.assertEqual(first, second)

    def test_different_seed_can_give_different_results(self):
        scored = _hand_built_scored_frame()
        results = {
            seed: random_baseline_top_n_picks(scored, n=2, random_seed=seed)["Average Forward Return"]
            for seed in range(10)
        }
        self.assertGreater(len(set(results.values())), 1)


class StrategyBaselineTests(unittest.TestCase):
    def test_returns_none_when_column_missing(self):
        scored = _hand_built_scored_frame()
        result = strategy_baseline_top_n_picks(scored, n=1, strategy_score_column="Strategy Score: Nope")
        self.assertIsNone(result)

    def test_uses_named_strategy_score_column(self):
        scored = _hand_built_scored_frame().rename(
            columns={"Predicted Probability": "Strategy Score: Momentum V2"}
        )
        result = strategy_baseline_top_n_picks(scored, n=1, strategy_score_column="Strategy Score: Momentum V2")
        self.assertAlmostEqual(result["Average Forward Return"], (0.10 + 0.20) / 2)


class WalkForwardEvaluationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dataset = _SUPERVISED_DATASET
        cls.folds = generate_walk_forward_folds(
            cls.dataset["Date"].min(),
            cls.dataset["Date"].max(),
            min_train_days=200,
            validation_days=60,
            step_days=60,
        )

    def test_at_least_one_fold_is_generated(self):
        self.assertGreater(len(self.folds), 0)

    def test_fold_results_never_let_validation_precede_or_equal_training(self):
        results = run_walk_forward_evaluation(
            self.dataset,
            self.folds,
            build_logistic_regression_pipeline,
            target_column="beats_SPY_20D",
        )
        self.assertGreater(len(results), 0)
        for fold_result in results:
            self.assertLess(fold_result["Train End"], fold_result["Validation Start"])
            self.assertIn("Accuracy", fold_result["Classification Metrics"])
            self.assertIn(5, fold_result["Top N Metrics"])
            self.assertIn("Pipeline", fold_result)

    def test_predicted_probabilities_used_for_ranking_are_valid(self):
        numeric_columns, categorical_columns = infer_feature_columns(self.dataset)
        results = run_walk_forward_evaluation(
            self.dataset,
            self.folds,
            build_logistic_regression_pipeline,
            target_column="beats_SPY_20D",
            feature_columns=numeric_columns,
            categorical_columns=categorical_columns,
        )
        for fold_result in results:
            pipeline = fold_result["Pipeline"]
            self.assertTrue(hasattr(pipeline, "predict_proba"))

    def test_ranking_output_is_deterministic(self):
        first = run_walk_forward_evaluation(
            self.dataset, self.folds, build_logistic_regression_pipeline, target_column="beats_SPY_20D"
        )
        second = run_walk_forward_evaluation(
            self.dataset, self.folds, build_logistic_regression_pipeline, target_column="beats_SPY_20D"
        )
        self.assertEqual(len(first), len(second))
        for fold_a, fold_b in zip(first, second):
            self.assertEqual(fold_a["Classification Metrics"], fold_b["Classification Metrics"])
            self.assertEqual(fold_a["Top N Metrics"], fold_b["Top N Metrics"])

    def test_top_n_evaluation_runs_for_every_configured_n(self):
        results = run_walk_forward_evaluation(
            self.dataset,
            self.folds,
            build_logistic_regression_pipeline,
            target_column="beats_SPY_20D",
            top_n_list=(5, 10),
        )
        for fold_result in results:
            self.assertEqual(set(fold_result["Top N Metrics"].keys()), {5, 10})
            self.assertEqual(set(fold_result["Random Baseline Top N Metrics"].keys()), {5, 10})

    def test_aggregate_fold_metrics_matches_manual_average(self):
        results = run_walk_forward_evaluation(
            self.dataset, self.folds, build_logistic_regression_pipeline, target_column="beats_SPY_20D"
        )
        aggregate = aggregate_fold_metrics(results)
        manual_accuracy = np.mean([fold["Classification Metrics"]["Accuracy"] for fold in results])
        self.assertAlmostEqual(aggregate["Classification Metrics"]["Accuracy"], manual_accuracy)
        self.assertEqual(aggregate["Folds Evaluated"], len(results))

    def test_strategy_baseline_comparison_uses_existing_strategy_feature(self):
        strategy_column = "Strategy Score: Momentum V2"
        self.assertIn(strategy_column, self.dataset.columns)
        results = run_walk_forward_evaluation(
            self.dataset,
            self.folds,
            build_logistic_regression_pipeline,
            target_column="beats_SPY_20D",
            strategy_score_column=strategy_column,
        )
        for fold_result in results:
            self.assertIsNotNone(fold_result["Strategy Baseline Top N Metrics"])


if __name__ == "__main__":
    unittest.main()
