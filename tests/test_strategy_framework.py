import io
import json
import unittest
import zipfile

import pandas as pd

from backtesting.export import build_complete_backtest_package
from config import LONG_MOMENTUM_DAYS, MOVING_AVERAGE_DAYS
from strategies.baseline import REQUIRED_HISTORY_DAYS
from strategies.registry import available_strategy_names, get_strategy


class BaselineRegressionTests(unittest.TestCase):
    def test_registry_contains_only_real_baseline_strategy(self):
        self.assertEqual(available_strategy_names(), ["Baseline"])

    def test_baseline_required_history_days_is_correct(self):
        expected = max(LONG_MOMENTUM_DAYS + 1, MOVING_AVERAGE_DAYS)
        self.assertEqual(REQUIRED_HISTORY_DAYS, expected)
        self.assertEqual(get_strategy("Baseline").required_history_days, expected)
        self.assertEqual(get_strategy("Baseline").required_history_days, 21)

    def test_baseline_matches_pre_refactor_fixture(self):
        closes = [100 + index * 0.5 for index in range(30)]
        data = pd.DataFrame(
            {"Close": closes, "Open": closes, "Volume": [1000] * 30},
            index=pd.date_range("2025-01-01", periods=30),
        )
        result = get_strategy("Baseline").analyze("TEST", data)
        self.assertEqual(result["Signal"], "BUY")
        self.assertEqual(result["Score"], 3)
        self.assertAlmostEqual(result["Short Momentum"], 0.022321428571428572)
        self.assertAlmostEqual(result["Long Momentum"], 0.09569377990430622)
        self.assertAlmostEqual(result["Moving Average"], 109.75)
        self.assertAlmostEqual(result["Price"], 114.5)
        self.assertTrue(
            {"Ticker", "Score", "Signal", "Reason", "Factor Details"}
            <= result.keys()
        )


class ExportTests(unittest.TestCase):
    def test_complete_package_contains_all_required_files(self):
        result = {
            "Algorithm": "Baseline",
            "Test": 1,
            "Requested Start": pd.Timestamp("2025-01-01"),
            "Requested End": pd.Timestamp("2025-02-05"),
            "Actual Start": pd.Timestamp("2025-01-02"),
            "Actual End": pd.Timestamp("2025-02-03"),
            "Start Date": pd.Timestamp("2025-01-02"),
            "End Date": pd.Timestamp("2025-02-03"),
            "Total Return": 0.05,
            "Benchmark Return": 0.03,
            "Trades": [{"Ticker": "AAPL", "Action": "BUY"}],
            "Final Holdings": [{"Ticker": "AAPL", "Market Value": 20_000}],
        }
        settings = {
            "selected_strategies": ["Baseline"],
            "mode": "Test One Algorithm",
        }
        package = build_complete_backtest_package(
            [result],
            [(pd.Timestamp("2025-01-01"), pd.Timestamp("2025-02-01"))],
            settings,
        )
        with zipfile.ZipFile(io.BytesIO(package)) as archive:
            names = set(archive.namelist())
            self.assertEqual(
                names,
                {
                    "comparison_summary.csv",
                    "periods.csv",
                    "settings.json",
                    "baseline/summary.csv",
                    "baseline/test_01_trades.csv",
                    "baseline/test_01_holdings.csv",
                },
            )
            exported_settings = json.loads(archive.read("settings.json"))
            self.assertEqual(exported_settings["selected_strategies"], ["Baseline"])
            summary = pd.read_csv(io.BytesIO(archive.read("baseline/summary.csv")))
            self.assertEqual(summary.loc[0, "Requested Start"], "2025-01-01")
            self.assertEqual(summary.loc[0, "Actual End"], "2025-02-03")
            periods = pd.read_csv(io.BytesIO(archive.read("periods.csv")))
            self.assertEqual(periods.loc[0, "Requested Start"], "2025-01-01")
            self.assertEqual(periods.loc[0, "Actual End"], "2025-02-03")


if __name__ == "__main__":
    unittest.main()
