import io
import json
import unittest
import zipfile

import pandas as pd

from backtesting.export import build_complete_backtest_package
from config import LONG_MOMENTUM_DAYS, MOVING_AVERAGE_DAYS
from strategies.baseline import REQUIRED_HISTORY_DAYS
from strategies.momentum_v2 import REQUIRED_HISTORY_DAYS as MOMENTUM_V2_REQUIRED_HISTORY_DAYS
from strategies.registry import available_strategy_names, get_strategy


def _linear_closes(length, slope=0.5, start=100.0):
    return [start + index * slope for index in range(length)]


def _price_frame(closes, start="2025-01-01"):
    return pd.DataFrame(
        {"Close": closes, "Open": closes, "Volume": [1_000] * len(closes)},
        index=pd.date_range(start, periods=len(closes)),
    )


class BaselineRegressionTests(unittest.TestCase):
    def test_registry_contains_baseline_and_momentum_v2(self):
        self.assertEqual(available_strategy_names(), ["Baseline", "Momentum V2"])

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


class MomentumV2Tests(unittest.TestCase):
    def test_required_history_days_is_at_least_121(self):
        self.assertGreaterEqual(MOMENTUM_V2_REQUIRED_HISTORY_DAYS, 121)
        self.assertEqual(
            get_strategy("Momentum V2").required_history_days,
            MOMENTUM_V2_REQUIRED_HISTORY_DAYS,
        )

    def test_calculations_use_only_the_rows_it_was_given(self):
        """analyze() must not depend on anything beyond its input DataFrame."""
        shared_prefix = _linear_closes(125, slope=0.6)
        # Two datasets share the first 125 rows but diverge afterward with a
        # large price spike that is never passed to analyze().
        continued_calm = shared_prefix + _linear_closes(5, slope=0.6, start=shared_prefix[-1] + 0.6)
        continued_spike = shared_prefix + [shared_prefix[-1] * 5] * 5

        calm_result = get_strategy("Momentum V2").analyze(
            "CALM", _price_frame(continued_calm).iloc[:125]
        )
        spike_result = get_strategy("Momentum V2").analyze(
            "SPIKE", _price_frame(continued_spike).iloc[:125]
        )

        self.assertIsNotNone(calm_result)
        self.assertIsNotNone(spike_result)
        for key in (
            "Momentum 20D",
            "Momentum 60D",
            "Momentum 120D",
            "Moving Average 50D",
            "Volatility 20D",
            "Final Score",
        ):
            self.assertAlmostEqual(calm_result[key], spike_result[key])

    def test_buys_a_sustained_uptrend(self):
        closes = _linear_closes(130, slope=0.8)
        result = get_strategy("Momentum V2").analyze("UP", _price_frame(closes))
        self.assertIsNotNone(result)
        self.assertEqual(result["Signal"], "BUY")
        self.assertGreater(result["Momentum 60D"], 0)
        self.assertGreater(result["Momentum 120D"], 0)
        self.assertTrue(result["Above MA50"])

    def test_does_not_buy_a_clear_decline(self):
        closes = _linear_closes(130, slope=-0.8, start=250.0)
        result = get_strategy("Momentum V2").analyze("DOWN", _price_frame(closes))
        self.assertIsNotNone(result)
        self.assertNotEqual(result["Signal"], "BUY")
        self.assertLess(result["Momentum 60D"], 0)
        self.assertLess(result["Momentum 120D"], 0)
        self.assertFalse(result["Above MA50"])

    def test_increasing_volatility_reduces_score_for_similar_momentum(self):
        # 130 rows; perturb positions -19..-2 (18 points) with a zero-sum
        # zigzag so the boundary anchors used by every momentum/trend/MA
        # calculation (index -1, -20, -21, -61, -121) are untouched, isolating
        # the effect of volatility on the final score.
        base_closes = _linear_closes(130, slope=0.5)
        calm_frame = _price_frame(list(base_closes))

        volatile_closes = list(base_closes)
        amplitude = 3.0
        perturb_start = len(volatile_closes) - 19
        perturb_end = len(volatile_closes) - 1  # exclusive; leaves index -1 untouched
        for offset, position in enumerate(range(perturb_start, perturb_end)):
            sign = 1 if offset % 2 == 0 else -1
            volatile_closes[position] = base_closes[position] + sign * amplitude
        volatile_frame = _price_frame(volatile_closes)

        calm_result = get_strategy("Momentum V2").analyze("CALM", calm_frame)
        volatile_result = get_strategy("Momentum V2").analyze("VOL", volatile_frame)

        self.assertIsNotNone(calm_result)
        self.assertIsNotNone(volatile_result)
        # The zero-sum perturbation leaves momentum and trend unchanged.
        self.assertAlmostEqual(
            calm_result["Raw Momentum Score"], volatile_result["Raw Momentum Score"]
        )
        self.assertGreater(
            volatile_result["Volatility 20D"], calm_result["Volatility 20D"]
        )
        self.assertGreater(
            volatile_result["Volatility Penalty"], calm_result["Volatility Penalty"]
        )
        self.assertLess(volatile_result["Final Score"], calm_result["Final Score"])

    def test_higher_momentum_scores_higher_with_similar_other_factors(self):
        weaker = get_strategy("Momentum V2").analyze(
            "WEAK", _price_frame(_linear_closes(130, slope=0.3))
        )
        stronger = get_strategy("Momentum V2").analyze(
            "STRONG", _price_frame(_linear_closes(130, slope=1.0))
        )
        self.assertIsNotNone(weaker)
        self.assertIsNotNone(stronger)
        self.assertGreater(stronger["Momentum 120D"], weaker["Momentum 120D"])
        self.assertGreater(stronger["Final Score"], weaker["Final Score"])
        self.assertGreater(
            get_strategy("Momentum V2").rank_key(stronger),
            get_strategy("Momentum V2").rank_key(weaker),
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
