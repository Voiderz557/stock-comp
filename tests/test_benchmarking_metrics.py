"""Tests for `benchmarking.metrics`."""

import unittest

import pandas as pd

from benchmarking.metrics import (
    build_aggregate_table,
    build_period_row,
    compute_aggregate_metrics,
    compute_max_drawdown,
    compute_turnover,
)


def _period_row(test, total_return, benchmark_return=0.0, trades=5, drawdown=-0.05, turnover=0.5):
    return {
        "Method": "Test Method",
        "Test": test,
        "Requested Start": pd.Timestamp("2020-01-01").date(),
        "Requested End": pd.Timestamp("2020-02-01").date(),
        "Actual Start": pd.Timestamp("2020-01-01"),
        "Actual End": pd.Timestamp("2020-02-01"),
        "Total Return": total_return,
        "Benchmark Return": benchmark_return,
        "Excess Return": total_return - benchmark_return,
        "Number of Trades": trades,
        "Max Drawdown": drawdown,
        "Turnover": turnover,
    }


class ThresholdMetricsTests(unittest.TestCase):
    def test_exact_20_percent_counts_toward_ge_20_percent(self):
        rows = [_period_row(1, 0.20), _period_row(2, 0.19), _period_row(3, 0.21)]
        metrics = compute_aggregate_metrics(rows)
        self.assertAlmostEqual(metrics["P(Return >= 20%)"], 2 / 3)

    def test_all_thresholds_present_and_monotonically_non_increasing(self):
        rows = [_period_row(i, return_value) for i, return_value in enumerate([0.05, 0.12, 0.18, 0.22, 0.31])]
        metrics = compute_aggregate_metrics(rows)
        probabilities = [
            metrics["P(Return >= 10%)"],
            metrics["P(Return >= 15%)"],
            metrics["P(Return >= 20%)"],
            metrics["P(Return >= 25%)"],
            metrics["P(Return >= 30%)"],
        ]
        for earlier, later in zip(probabilities, probabilities[1:]):
            self.assertGreaterEqual(earlier, later)

    def test_failed_tests_are_excluded_from_denominator(self):
        # Only 2 rows are "completed" - a failed/skipped test never produces
        # a period row in the first place, so the denominator here is
        # naturally just the completed rows.
        rows = [_period_row(1, 0.25), _period_row(2, 0.05)]
        metrics = compute_aggregate_metrics(rows)
        self.assertEqual(metrics["Periods Tested"], 2)
        self.assertAlmostEqual(metrics["P(Return >= 20%)"], 0.5)


class AggregateMetricsTests(unittest.TestCase):
    def test_beat_spy_and_positive_period_rates(self):
        rows = [
            _period_row(1, 0.10, benchmark_return=0.05),
            _period_row(2, -0.02, benchmark_return=0.01),
            _period_row(3, 0.03, benchmark_return=0.03),
        ]
        metrics = compute_aggregate_metrics(rows)
        self.assertAlmostEqual(metrics["Beat SPY %"], 1 / 3)
        self.assertAlmostEqual(metrics["Positive Period %"], 2 / 3)

    def test_best_and_worst_period_identified_correctly(self):
        rows = [_period_row(1, 0.10), _period_row(2, -0.20), _period_row(3, 0.30)]
        metrics = compute_aggregate_metrics(rows)
        self.assertEqual(metrics["Best Period Test"], 3)
        self.assertEqual(metrics["Worst Period Test"], 2)

    def test_sharpe_and_sortino_are_nan_with_too_few_observations(self):
        rows = [_period_row(1, 0.10), _period_row(2, 0.05)]
        metrics = compute_aggregate_metrics(rows)
        self.assertTrue(pd.isna(metrics["Sharpe Ratio"]))
        self.assertTrue(pd.isna(metrics["Sortino Ratio"]))

    def test_sharpe_ratio_is_computed_with_enough_observations(self):
        rows = [_period_row(i, value) for i, value in enumerate([0.05, 0.03, 0.07, 0.02, 0.06])]
        metrics = compute_aggregate_metrics(rows)
        self.assertFalse(pd.isna(metrics["Sharpe Ratio"]))

    def test_extreme_winner_dependence_is_flagged(self):
        # 9 modest losses and one enormous winner: the plain average is
        # positive and dominated by that one period.
        rows = [_period_row(i, -0.01) for i in range(9)] + [_period_row(9, 5.0)]
        metrics = compute_aggregate_metrics(rows)
        self.assertTrue(metrics["Extreme Winner Dependent"])

    def test_no_extreme_winner_dependence_when_returns_are_even(self):
        rows = [_period_row(i, 0.05) for i in range(10)]
        metrics = compute_aggregate_metrics(rows)
        self.assertFalse(metrics["Extreme Winner Dependent"])

    def test_empty_input_returns_empty_dict(self):
        self.assertEqual(compute_aggregate_metrics([]), {})


class DrawdownAndTurnoverTests(unittest.TestCase):
    def test_max_drawdown_is_computed_correctly(self):
        history = [
            {"Portfolio Value": 100_000},
            {"Portfolio Value": 120_000},
            {"Portfolio Value": 90_000},
            {"Portfolio Value": 110_000},
        ]
        drawdown = compute_max_drawdown(history)
        self.assertAlmostEqual(drawdown, 90_000 / 120_000 - 1)

    def test_turnover_uses_starting_cash_as_denominator(self):
        result = {
            "Trades": [
                {"Shares": 10, "Price": 100.0},
                {"Shares": 5, "Price": 50.0},
            ],
            "Starting Value": 10_000,
        }
        turnover = compute_turnover(result)
        self.assertAlmostEqual(turnover, (1000 + 250) / 10_000)


class BuildTablesTests(unittest.TestCase):
    def test_build_period_row_matches_expected_shape(self):
        result = {
            "Method": "Baseline",
            "Requested Start": pd.Timestamp("2020-01-01"),
            "Requested End": pd.Timestamp("2020-02-01"),
            "Actual Start": pd.Timestamp("2020-01-02"),
            "Actual End": pd.Timestamp("2020-01-31"),
            "Total Return": 0.10,
            "Benchmark Return": 0.05,
            "Trades": [{"Shares": 1, "Price": 100.0}],
            "Portfolio History": [{"Portfolio Value": 100_000}, {"Portfolio Value": 110_000}],
            "Starting Value": 100_000,
        }
        row = build_period_row(result, test_number=1)
        self.assertEqual(row["Excess Return"], 0.05)
        self.assertEqual(row["Number of Trades"], 1)

    def test_build_aggregate_table_has_one_row_per_method(self):
        rows = [_period_row(1, 0.1), _period_row(2, 0.2)]
        rows[1]["Method"] = "Other Method"
        table = build_aggregate_table(pd.DataFrame(rows))
        self.assertEqual(len(table), 2)
        self.assertIn("Method", table.columns)


if __name__ == "__main__":
    unittest.main()
