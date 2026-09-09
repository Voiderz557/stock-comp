import io
import unittest
import zipfile

import pandas as pd

from backtesting.export import (
    COMPETITION_RETURN_COLUMN,
    build_comparison_summary,
    build_complete_backtest_package,
)


def _completed_result(
    algorithm, test, total_return, benchmark_return, trade_count=1
):
    trades = [{"Ticker": "AAPL", "Action": "BUY"}] * trade_count
    return {
        "Algorithm": algorithm,
        "Test": test,
        "Requested Start": pd.Timestamp("2025-01-01"),
        "Requested End": pd.Timestamp("2025-06-01"),
        "Actual Start": pd.Timestamp("2025-01-02"),
        "Actual End": pd.Timestamp("2025-05-30"),
        "Start Date": pd.Timestamp("2025-01-02"),
        "End Date": pd.Timestamp("2025-05-30"),
        "Total Return": total_return,
        "Benchmark Return": benchmark_return,
        "Trades": trades,
        "Final Holdings": [{"Ticker": "AAPL", "Market Value": 20_000}],
    }


def _failed_result(algorithm, test, error="download failed"):
    return {
        "Algorithm": algorithm,
        "Test": test,
        "Requested Start": pd.Timestamp("2025-01-01"),
        "Requested End": pd.Timestamp("2025-06-01"),
        "Error": error,
    }


class ComparisonSummaryTests(unittest.TestCase):
    def test_existing_comparison_metrics_remain_unchanged(self):
        results = [
            _completed_result("Baseline", 1, 0.10, 0.05, trade_count=2),
            _completed_result("Baseline", 2, 0.20, 0.05, trade_count=1),
            _completed_result("Baseline", 3, -0.05, 0.00, trade_count=0),
        ]
        row = build_comparison_summary(results).iloc[0]

        self.assertEqual(row["Algorithm"], "Baseline")
        self.assertAlmostEqual(row["Average Return"], (0.10 + 0.20 - 0.05) / 3)
        self.assertAlmostEqual(row["Median Return"], 0.10)
        self.assertAlmostEqual(
            row["Average Benchmark Return"], (0.05 + 0.05 + 0.00) / 3
        )
        self.assertAlmostEqual(row["Average Excess Return"], 0.05)
        self.assertAlmostEqual(row["Win Rate vs Benchmark"], 2 / 3)
        self.assertEqual(row["Best Test"], "Test 2: +20.00%")
        self.assertEqual(row["Worst Test"], "Test 3: -5.00%")
        self.assertAlmostEqual(row["Average Number of Trades"], 1.0)

    def test_threshold_percentages_are_calculated_correctly(self):
        results = [
            _completed_result("Aggressive Momentum V1", 1, 0.09, 0.00),
            _completed_result("Aggressive Momentum V1", 2, 0.10, 0.00),
            _completed_result("Aggressive Momentum V1", 3, 0.15, 0.00),
            _completed_result("Aggressive Momentum V1", 4, 0.20, 0.00),
            _completed_result("Aggressive Momentum V1", 5, 0.31, 0.00),
        ]
        row = build_comparison_summary(results).iloc[0]

        self.assertAlmostEqual(row["Return >= 10%"], 4 / 5)
        self.assertAlmostEqual(row["Return >= 15%"], 3 / 5)
        self.assertAlmostEqual(row["Return >= 20%"], 2 / 5)
        self.assertAlmostEqual(row["Return >= 25%"], 1 / 5)
        self.assertAlmostEqual(row["Return >= 30%"], 1 / 5)
        self.assertEqual(COMPETITION_RETURN_COLUMN, "Return >= 20%")

        self.assertAlmostEqual(row["Median Excess Return"], 0.15)
        self.assertAlmostEqual(row["Beat Benchmark %"], 1.0)
        self.assertAlmostEqual(row["Positive Return %"], 1.0)
        self.assertAlmostEqual(row["Best Return"], 0.31)
        self.assertAlmostEqual(row["Worst Return"], 0.09)

    def test_failed_tests_are_excluded_from_denominator(self):
        results = [
            _completed_result("Baseline", 1, 0.20, 0.00),
            _completed_result("Baseline", 2, 0.05, 0.00),
            _failed_result("Baseline", 3),
            {
                "Algorithm": "Baseline",
                "Test": 4,
                "Total Return": None,
                "Benchmark Return": 0.01,
                "Trades": [],
            },
        ]
        summary = build_comparison_summary(results)
        self.assertEqual(len(summary), 1)
        row = summary.iloc[0]
        self.assertAlmostEqual(row["Return >= 20%"], 0.5)
        self.assertAlmostEqual(row["Positive Return %"], 1.0)
        self.assertAlmostEqual(row["Average Return"], 0.125)
        self.assertAlmostEqual(row["Average Number of Trades"], 1.0)

    def test_exact_20_percent_return_counts_as_at_least_20_percent(self):
        results = [
            _completed_result("Momentum V2", 1, 0.20, 0.00),
            _completed_result("Momentum V2", 2, 0.199, 0.00),
            _completed_result("Momentum V2", 3, 0.25, 0.00),
        ]
        row = build_comparison_summary(results).iloc[0]
        self.assertAlmostEqual(row["Return >= 20%"], 2 / 3)
        self.assertAlmostEqual(row["Return >= 25%"], 1 / 3)

    def test_comparison_csv_includes_new_metrics_without_changing_zip_files(self):
        result = _completed_result("Baseline", 1, 0.20, 0.03)
        settings = {
            "selected_strategies": ["Baseline"],
            "mode": "Compare Algorithms",
        }
        package = build_complete_backtest_package(
            [result],
            [(pd.Timestamp("2025-01-01"), pd.Timestamp("2025-06-01"))],
            settings,
        )
        with zipfile.ZipFile(io.BytesIO(package)) as archive:
            self.assertEqual(
                set(archive.namelist()),
                {
                    "comparison_summary.csv",
                    "periods.csv",
                    "settings.json",
                    "baseline/summary.csv",
                    "baseline/test_01_trades.csv",
                    "baseline/test_01_holdings.csv",
                },
            )
            comparison = pd.read_csv(
                io.BytesIO(archive.read("comparison_summary.csv"))
            )
            for column in (
                "Average Return",
                "Median Return",
                "Average Excess Return",
                "Median Excess Return",
                "Win Rate vs Benchmark",
                "Beat Benchmark %",
                "Positive Return %",
                "Best Return",
                "Worst Return",
                "Average Number of Trades",
                "Return >= 10%",
                "Return >= 15%",
                "Return >= 20%",
                "Return >= 25%",
                "Return >= 30%",
            ):
                self.assertIn(column, comparison.columns)
            self.assertAlmostEqual(comparison.loc[0, "Return >= 20%"], 1.0)


if __name__ == "__main__":
    unittest.main()
