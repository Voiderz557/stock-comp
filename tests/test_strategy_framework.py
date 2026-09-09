import io
import json
import unittest
import zipfile

import pandas as pd

from backtesting.export import build_complete_backtest_package
from config import LONG_MOMENTUM_DAYS, MOVING_AVERAGE_DAYS
from strategies.aggressive_momentum_v1 import (
    REQUIRED_HISTORY_DAYS as AGGRESSIVE_REQUIRED_HISTORY_DAYS,
    VOLATILITY_PENALTY_MULTIPLIER as AGGRESSIVE_VOLATILITY_PENALTY,
    WEIGHT_BREAKOUT,
    WEIGHT_VOLUME,
)
from strategies.baseline import REQUIRED_HISTORY_DAYS
from strategies.momentum_v2 import (
    REQUIRED_HISTORY_DAYS as MOMENTUM_V2_REQUIRED_HISTORY_DAYS,
    VOLATILITY_PENALTY_MULTIPLIER as MOMENTUM_V2_VOLATILITY_PENALTY,
)
from strategies.breakout_volume_v1 import (
    REQUIRED_HISTORY_DAYS as BREAKOUT_VOLUME_REQUIRED_HISTORY_DAYS,
    WEIGHT_BREAKOUT_20D,
    WEIGHT_BREAKOUT_60D,
    WEIGHT_VOLUME as BREAKOUT_WEIGHT_VOLUME,
    volume_confirmation_from_ratio,
)
from strategies.relative_strength_momentum_v1 import (
    REQUIRED_HISTORY_DAYS as RELATIVE_STRENGTH_REQUIRED_HISTORY_DAYS,
)
from strategies.registry import available_strategy_names, get_strategy, invoke_analyze


def _linear_closes(length, slope=0.5, start=100.0):
    return [start + index * slope for index in range(length)]


def _price_frame(closes, start="2025-01-01", volumes=None):
    if volumes is None:
        volumes = [1_000] * len(closes)
    return pd.DataFrame(
        {"Close": closes, "Open": closes, "Volume": volumes},
        index=pd.date_range(start, periods=len(closes)),
    )


class BaselineRegressionTests(unittest.TestCase):
    def test_registry_contains_baseline_and_momentum_v2(self):
        self.assertEqual(
            available_strategy_names(),
            [
                "Baseline",
                "Momentum V2",
                "Aggressive Momentum V1",
                "Relative Strength Momentum V1",
                "Breakout Volume V1",
            ],
        )

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


class AggressiveMomentumV1Tests(unittest.TestCase):
    def _analyze(self, ticker, closes, volumes=None):
        return get_strategy("Aggressive Momentum V1").analyze(
            ticker, _price_frame(closes, volumes=volumes)
        )

    def test_registers_with_required_history_days(self):
        self.assertIn("Aggressive Momentum V1", available_strategy_names())
        self.assertGreaterEqual(AGGRESSIVE_REQUIRED_HISTORY_DAYS, 121)
        self.assertEqual(
            get_strategy("Aggressive Momentum V1").required_history_days,
            AGGRESSIVE_REQUIRED_HISTORY_DAYS,
        )
        self.assertLess(AGGRESSIVE_VOLATILITY_PENALTY, MOMENTUM_V2_VOLATILITY_PENALTY)

    def test_required_history_is_sufficient_for_all_factors(self):
        too_short = self._analyze(
            "THIN", _linear_closes(AGGRESSIVE_REQUIRED_HISTORY_DAYS - 1, slope=0.8)
        )
        self.assertIsNone(too_short)

        result = self._analyze(
            "OK", _linear_closes(AGGRESSIVE_REQUIRED_HISTORY_DAYS, slope=0.8)
        )
        self.assertIsNotNone(result)
        for key in (
            "Price",
            "Momentum 20D",
            "Momentum 60D",
            "Momentum 120D",
            "MA50",
            "Above MA50",
            "Breakout Strength",
            "Recent Volume Average",
            "Volume Baseline Average",
            "Volume Confirmation",
            "Volatility 20D",
            "Raw Score",
            "Volatility Penalty",
            "Final Score",
        ):
            self.assertIn(key, result["Factor Details"])
            self.assertFalse(pd.isna(result["Factor Details"][key]))

    def test_result_has_standard_fields(self):
        result = self._analyze("UP", _linear_closes(130, slope=0.8))
        self.assertIsNotNone(result)
        self.assertTrue(
            {"Ticker", "Score", "Signal", "Reason", "Factor Details"}
            <= result.keys()
        )

    def test_strong_sustained_uptrend_produces_buy(self):
        result = self._analyze("UP", _linear_closes(130, slope=0.8))
        self.assertIsNotNone(result)
        self.assertEqual(result["Signal"], "BUY")
        self.assertGreater(result["Momentum 20D"], 0)
        self.assertGreater(result["Momentum 60D"], 0)
        self.assertTrue(result["Above MA50"])
        self.assertGreaterEqual(result["Breakout Strength"], 0.70)

    def test_clear_downtrend_does_not_produce_buy(self):
        result = self._analyze("DOWN", _linear_closes(130, slope=-0.8, start=250.0))
        self.assertIsNotNone(result)
        self.assertNotEqual(result["Signal"], "BUY")
        self.assertLess(result["Momentum 20D"], 0)
        self.assertLess(result["Momentum 60D"], 0)
        self.assertFalse(result["Above MA50"])

    def test_stronger_recent_momentum_increases_score(self):
        weaker = self._analyze("WEAK", _linear_closes(130, slope=0.3))
        stronger = self._analyze("STRONG", _linear_closes(130, slope=1.0))
        self.assertIsNotNone(weaker)
        self.assertIsNotNone(stronger)
        self.assertGreater(stronger["Momentum 20D"], weaker["Momentum 20D"])
        self.assertGreater(stronger["Final Score"], weaker["Final Score"])
        self.assertGreater(
            get_strategy("Aggressive Momentum V1").rank_key(stronger),
            get_strategy("Aggressive Momentum V1").rank_key(weaker),
        )

    def test_stronger_breakout_increases_score(self):
        # Same momentum anchors (index -1, -21, -61, -121) and identical
        # volume; only an interior 20-day high changes, which lowers
        # breakout strength without changing the momentum inputs.
        base_closes = _linear_closes(130, slope=0.5)
        weaker_closes = list(base_closes)
        weaker_closes[-10] = base_closes[-1] * 1.15

        stronger = self._analyze("NEAR_HIGH", base_closes)
        weaker = self._analyze("PULLED_BACK", weaker_closes)
        self.assertIsNotNone(weaker)
        self.assertIsNotNone(stronger)
        self.assertAlmostEqual(stronger["Momentum 20D"], weaker["Momentum 20D"])
        self.assertAlmostEqual(stronger["Momentum 60D"], weaker["Momentum 60D"])
        self.assertAlmostEqual(stronger["Momentum 120D"], weaker["Momentum 120D"])
        self.assertGreater(stronger["Breakout Strength"], weaker["Breakout Strength"])
        self.assertAlmostEqual(
            stronger["Raw Score"] - weaker["Raw Score"],
            WEIGHT_BREAKOUT
            * (stronger["Breakout Strength"] - weaker["Breakout Strength"]),
        )
        self.assertGreater(stronger["Final Score"], weaker["Final Score"])

    def test_stronger_volume_confirmation_increases_score(self):
        closes = _linear_closes(130, slope=0.5)
        baseline_volumes = [1_000] * 130
        stronger_volumes = [1_000] * 125 + [2_000] * 5

        weaker = self._analyze("AVG_VOL", closes, volumes=baseline_volumes)
        stronger = self._analyze("HIGH_VOL", closes, volumes=stronger_volumes)
        self.assertIsNotNone(weaker)
        self.assertIsNotNone(stronger)
        self.assertGreater(
            stronger["Volume Confirmation"], weaker["Volume Confirmation"]
        )
        self.assertAlmostEqual(
            stronger["Raw Score"] - weaker["Raw Score"],
            WEIGHT_VOLUME
            * (stronger["Volume Confirmation"] - weaker["Volume Confirmation"]),
        )
        self.assertGreater(stronger["Final Score"], weaker["Final Score"])

    def test_higher_volatility_reduces_score(self):
        # Perturb interiors downward only so the current close remains the
        # 20-day high. Momentum anchors and breakout strength stay put;
        # only volatility (and therefore the penalty) should change.
        base_closes = _linear_closes(130, slope=0.5)
        volatile_closes = list(base_closes)
        amplitude = 3.0
        perturb_start = len(volatile_closes) - 19
        perturb_end = len(volatile_closes) - 1
        for position in range(perturb_start, perturb_end):
            volatile_closes[position] = base_closes[position] - amplitude

        calm_result = self._analyze("CALM", base_closes)
        volatile_result = self._analyze("VOL", volatile_closes)

        self.assertIsNotNone(calm_result)
        self.assertIsNotNone(volatile_result)
        self.assertAlmostEqual(calm_result["Raw Score"], volatile_result["Raw Score"])
        self.assertGreater(
            volatile_result["Volatility 20D"], calm_result["Volatility 20D"]
        )
        self.assertGreater(
            volatile_result["Volatility Penalty"], calm_result["Volatility Penalty"]
        )
        self.assertLess(volatile_result["Final Score"], calm_result["Final Score"])

    def test_calculations_use_only_the_rows_it_was_given(self):
        shared_prefix = _linear_closes(125, slope=0.6)
        continued_calm = shared_prefix + _linear_closes(
            5, slope=0.6, start=shared_prefix[-1] + 0.6
        )
        continued_spike = shared_prefix + [shared_prefix[-1] * 5] * 5

        calm_result = get_strategy("Aggressive Momentum V1").analyze(
            "CALM", _price_frame(continued_calm).iloc[:125]
        )
        spike_result = get_strategy("Aggressive Momentum V1").analyze(
            "SPIKE", _price_frame(continued_spike).iloc[:125]
        )

        self.assertIsNotNone(calm_result)
        self.assertIsNotNone(spike_result)
        for key in (
            "Momentum 20D",
            "Momentum 60D",
            "Momentum 120D",
            "MA50",
            "Breakout Strength",
            "Volume Confirmation",
            "Volatility 20D",
            "Final Score",
        ):
            self.assertAlmostEqual(calm_result[key], spike_result[key])


class RelativeStrengthMomentumV1Tests(unittest.TestCase):
    def _analyze(self, ticker, stock_closes, benchmark_closes):
        return get_strategy("Relative Strength Momentum V1").analyze(
            ticker,
            _price_frame(stock_closes),
            benchmark_data=_price_frame(benchmark_closes),
        )

    def test_registers_with_required_history_days(self):
        definition = get_strategy("Relative Strength Momentum V1")
        self.assertIn("Relative Strength Momentum V1", available_strategy_names())
        self.assertGreaterEqual(RELATIVE_STRENGTH_REQUIRED_HISTORY_DAYS, 121)
        self.assertEqual(
            definition.required_history_days, RELATIVE_STRENGTH_REQUIRED_HISTORY_DAYS
        )
        self.assertEqual(definition.benchmark_ticker, "QQQ")

    def test_required_history_is_sufficient_for_stock_and_benchmark(self):
        days = RELATIVE_STRENGTH_REQUIRED_HISTORY_DAYS
        stock = _linear_closes(days, slope=0.8)
        benchmark = _linear_closes(days, slope=0.2)
        self.assertIsNone(
            self._analyze("THIN_STOCK", stock[:-1], benchmark)
        )
        self.assertIsNone(
            self._analyze("THIN_BENCH", stock, benchmark[:-1])
        )
        result = self._analyze("OK", stock, benchmark)
        self.assertIsNotNone(result)
        for key in (
            "Price",
            "Momentum 20D",
            "Momentum 60D",
            "Momentum 120D",
            "Benchmark Momentum 20D",
            "Benchmark Momentum 60D",
            "Relative Strength 20D",
            "Relative Strength 60D",
            "MA50",
            "Above MA50",
            "Volatility 20D",
            "Raw Score",
            "Volatility Penalty",
            "Final Score",
        ):
            self.assertIn(key, result["Factor Details"])
            self.assertFalse(pd.isna(result["Factor Details"][key]))

    def test_stock_outperforming_benchmark_scores_higher(self):
        stock = _linear_closes(130, slope=0.6)
        weaker_benchmark = _linear_closes(130, slope=0.1)
        stronger_benchmark = _linear_closes(130, slope=0.4)
        outperforming = self._analyze("VS_WEAK", stock, weaker_benchmark)
        closer = self._analyze("VS_STRONG", stock, stronger_benchmark)
        self.assertIsNotNone(outperforming)
        self.assertIsNotNone(closer)
        self.assertGreater(
            outperforming["Relative Strength 60D"], closer["Relative Strength 60D"]
        )
        self.assertGreater(outperforming["Final Score"], closer["Final Score"])

    def test_stronger_benchmark_lowers_relative_strength_for_the_same_stock(self):
        stock = _linear_closes(130, slope=0.5)
        weaker_benchmark = _linear_closes(130, slope=0.1)
        stronger_benchmark = _linear_closes(130, slope=0.4)
        vs_weak = self._analyze("A", stock, weaker_benchmark)
        vs_strong = self._analyze("A", stock, stronger_benchmark)
        self.assertAlmostEqual(vs_weak["Momentum 20D"], vs_strong["Momentum 20D"])
        self.assertAlmostEqual(vs_weak["Momentum 60D"], vs_strong["Momentum 60D"])
        self.assertGreater(
            vs_strong["Benchmark Momentum 60D"], vs_weak["Benchmark Momentum 60D"]
        )
        self.assertLess(
            vs_strong["Relative Strength 20D"], vs_weak["Relative Strength 20D"]
        )
        self.assertLess(
            vs_strong["Relative Strength 60D"], vs_weak["Relative Strength 60D"]
        )

    def test_strong_outperforming_uptrend_produces_buy(self):
        result = self._analyze(
            "UP",
            _linear_closes(130, slope=0.8),
            _linear_closes(130, slope=0.2),
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["Signal"], "BUY")
        self.assertGreater(result["Momentum 20D"], 0)
        self.assertGreater(result["Momentum 60D"], 0)
        self.assertGreater(result["Relative Strength 60D"], 0)
        self.assertTrue(result["Above MA50"])

    def test_weak_underperformer_does_not_produce_buy(self):
        lagging = self._analyze(
            "LAG",
            _linear_closes(130, slope=0.2),
            _linear_closes(130, slope=0.8),
        )
        declining = self._analyze(
            "DOWN",
            _linear_closes(130, slope=-0.8, start=250.0),
            _linear_closes(130, slope=0.2),
        )
        self.assertIsNotNone(lagging)
        self.assertIsNotNone(declining)
        self.assertNotEqual(lagging["Signal"], "BUY")
        self.assertLess(lagging["Relative Strength 60D"], 0)
        self.assertNotEqual(declining["Signal"], "BUY")

    def test_calculations_use_only_the_rows_it_was_given(self):
        shared_stock = _linear_closes(125, slope=0.6)
        shared_benchmark = _linear_closes(125, slope=0.2)
        calm_stock = shared_stock + _linear_closes(
            5, slope=0.6, start=shared_stock[-1] + 0.6
        )
        spike_stock = shared_stock + [shared_stock[-1] * 5] * 5
        calm_benchmark = shared_benchmark + _linear_closes(
            5, slope=0.2, start=shared_benchmark[-1] + 0.2
        )
        spike_benchmark = shared_benchmark + [shared_benchmark[-1] * 5] * 5

        strategy = get_strategy("Relative Strength Momentum V1")
        calm_result = strategy.analyze(
            "CALM",
            _price_frame(calm_stock).iloc[:125],
            benchmark_data=_price_frame(calm_benchmark).iloc[:125],
        )
        spike_result = strategy.analyze(
            "SPIKE",
            _price_frame(spike_stock).iloc[:125],
            benchmark_data=_price_frame(spike_benchmark).iloc[:125],
        )
        self.assertIsNotNone(calm_result)
        self.assertIsNotNone(spike_result)
        for key in (
            "Momentum 20D",
            "Momentum 60D",
            "Momentum 120D",
            "Benchmark Momentum 20D",
            "Benchmark Momentum 60D",
            "Relative Strength 20D",
            "Relative Strength 60D",
            "MA50",
            "Volatility 20D",
            "Final Score",
        ):
            self.assertAlmostEqual(calm_result[key], spike_result[key])

    def test_invoke_analyze_does_not_break_existing_strategy_contract(self):
        data = _price_frame(_linear_closes(30, slope=0.5))
        direct = get_strategy("Baseline").analyze("TEST", data)
        wrapped = invoke_analyze(
            get_strategy("Baseline").analyze,
            "TEST",
            data,
            benchmark_data=_price_frame(_linear_closes(30, slope=0.1)),
        )
        self.assertEqual(direct["Signal"], wrapped["Signal"])
        self.assertEqual(direct["Score"], wrapped["Score"])


class BreakoutVolumeV1Tests(unittest.TestCase):
    def _analyze(self, ticker, closes, volumes=None):
        return get_strategy("Breakout Volume V1").analyze(
            ticker, _price_frame(closes, volumes=volumes)
        )

    def test_registers_with_required_history_days(self):
        definition = get_strategy("Breakout Volume V1")
        self.assertIn("Breakout Volume V1", available_strategy_names())
        self.assertGreaterEqual(BREAKOUT_VOLUME_REQUIRED_HISTORY_DAYS, 61)
        self.assertEqual(
            definition.required_history_days, BREAKOUT_VOLUME_REQUIRED_HISTORY_DAYS
        )

    def test_required_history_is_sufficient_for_all_factors(self):
        days = BREAKOUT_VOLUME_REQUIRED_HISTORY_DAYS
        self.assertIsNone(self._analyze("THIN", _linear_closes(days - 1, slope=0.8)))
        result = self._analyze("OK", _linear_closes(days, slope=0.8))
        self.assertIsNotNone(result)
        for key in (
            "Price",
            "Prior 20D High",
            "Prior 60D High",
            "Breakout 20D",
            "Breakout 60D",
            "Momentum 20D",
            "Momentum 60D",
            "MA50",
            "Above MA50",
            "Recent 5D Volume",
            "Average 20D Volume",
            "Volume Ratio",
            "Volatility 20D",
            "Raw Score",
            "Volatility Penalty",
            "Final Score",
        ):
            self.assertIn(key, result["Factor Details"])
            self.assertFalse(pd.isna(result["Factor Details"][key]))

    def test_clean_breakout_with_strong_volume_produces_buy(self):
        closes = _linear_closes(80, slope=0.8)
        volumes = [1_000] * 75 + [2_500] * 5
        result = self._analyze("BREAKOUT", closes, volumes=volumes)
        self.assertIsNotNone(result)
        self.assertEqual(result["Signal"], "BUY")
        self.assertGreaterEqual(result["Breakout 20D"], -0.02)
        self.assertGreater(result["Momentum 20D"], 0)
        self.assertGreater(result["Momentum 60D"], 0)
        self.assertTrue(result["Above MA50"])
        self.assertGreaterEqual(result["Volume Ratio"], 0.80)

    def test_stock_below_recent_highs_does_not_produce_buy(self):
        closes = _linear_closes(80, slope=0.8)
        prior_20d_high = max(closes[-21:-1])
        closes[-1] = prior_20d_high * 0.95
        result = self._analyze("PULLED_BACK", closes)
        self.assertIsNotNone(result)
        self.assertNotEqual(result["Signal"], "BUY")
        self.assertLess(result["Breakout 20D"], -0.02)

    def test_stronger_breakout_increases_score(self):
        base_closes = _linear_closes(80, slope=0.5)
        weaker_closes = list(base_closes)
        weaker_closes[-10] = base_closes[-1] * 1.15

        stronger = self._analyze("NEAR_HIGH", base_closes)
        weaker = self._analyze("CAPPED", weaker_closes)
        self.assertIsNotNone(stronger)
        self.assertIsNotNone(weaker)
        self.assertAlmostEqual(stronger["Momentum 20D"], weaker["Momentum 20D"])
        self.assertAlmostEqual(stronger["Momentum 60D"], weaker["Momentum 60D"])
        self.assertGreater(stronger["Breakout 20D"], weaker["Breakout 20D"])
        self.assertAlmostEqual(
            stronger["Raw Score"] - weaker["Raw Score"],
            WEIGHT_BREAKOUT_20D * (stronger["Breakout 20D"] - weaker["Breakout 20D"])
            + WEIGHT_BREAKOUT_60D * (stronger["Breakout 60D"] - weaker["Breakout 60D"]),
        )
        self.assertGreater(stronger["Final Score"], weaker["Final Score"])

    def test_stronger_volume_confirmation_increases_score(self):
        closes = _linear_closes(80, slope=0.5)
        baseline_volumes = [1_000] * 80
        stronger_volumes = [1_000] * 75 + [2_000] * 5

        weaker = self._analyze("AVG_VOL", closes, volumes=baseline_volumes)
        stronger = self._analyze("HIGH_VOL", closes, volumes=stronger_volumes)
        self.assertIsNotNone(weaker)
        self.assertIsNotNone(stronger)
        self.assertGreater(stronger["Volume Ratio"], weaker["Volume Ratio"])
        expected = BREAKOUT_WEIGHT_VOLUME * (
            volume_confirmation_from_ratio(stronger["Volume Ratio"], 1.0)
            - volume_confirmation_from_ratio(weaker["Volume Ratio"], 1.0)
        )
        self.assertAlmostEqual(stronger["Raw Score"] - weaker["Raw Score"], expected)
        self.assertGreater(stronger["Final Score"], weaker["Final Score"])

    def test_higher_volatility_lowers_score(self):
        # Leave current close, yesterday (the linear prior high), and the
        # momentum anchors (-21, -61) unchanged so only volatility moves.
        base_closes = _linear_closes(80, slope=0.5)
        volatile_closes = list(base_closes)
        amplitude = 0.2
        perturb_start = len(volatile_closes) - 19
        perturb_end = len(volatile_closes) - 2
        for offset, position in enumerate(range(perturb_start, perturb_end)):
            sign = 1 if offset % 2 == 0 else -1
            volatile_closes[position] = base_closes[position] + sign * amplitude

        calm_result = self._analyze("CALM", base_closes)
        volatile_result = self._analyze("VOL", volatile_closes)
        self.assertIsNotNone(calm_result)
        self.assertIsNotNone(volatile_result)
        self.assertAlmostEqual(calm_result["Breakout 20D"], volatile_result["Breakout 20D"])
        self.assertAlmostEqual(calm_result["Momentum 20D"], volatile_result["Momentum 20D"])
        self.assertGreater(
            volatile_result["Volatility 20D"], calm_result["Volatility 20D"]
        )
        self.assertLess(volatile_result["Final Score"], calm_result["Final Score"])

    def test_prior_high_calculations_exclude_the_current_row(self):
        closes = [100.0] * 70
        closes[-5] = 180.0
        closes[-1] = 250.0
        result = self._analyze("NEW_HIGH", closes)
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result["Prior 20D High"], 180.0)
        self.assertAlmostEqual(result["Prior 60D High"], 180.0)
        self.assertNotEqual(result["Prior 20D High"], result["Price"])
        self.assertAlmostEqual(result["Breakout 20D"], (250.0 / 180.0) - 1)

    def test_calculations_use_only_the_rows_it_was_given(self):
        shared_prefix = _linear_closes(70, slope=0.6)
        continued_calm = shared_prefix + _linear_closes(
            5, slope=0.6, start=shared_prefix[-1] + 0.6
        )
        continued_spike = shared_prefix + [shared_prefix[-1] * 5] * 5

        calm_result = get_strategy("Breakout Volume V1").analyze(
            "CALM", _price_frame(continued_calm).iloc[:70]
        )
        spike_result = get_strategy("Breakout Volume V1").analyze(
            "SPIKE", _price_frame(continued_spike).iloc[:70]
        )
        self.assertIsNotNone(calm_result)
        self.assertIsNotNone(spike_result)
        for key in (
            "Prior 20D High",
            "Prior 60D High",
            "Breakout 20D",
            "Breakout 60D",
            "Momentum 20D",
            "Momentum 60D",
            "MA50",
            "Volume Ratio",
            "Volatility 20D",
            "Final Score",
        ):
            self.assertAlmostEqual(calm_result[key], spike_result[key])


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
            comparison = pd.read_csv(
                io.BytesIO(archive.read("comparison_summary.csv"))
            )
            self.assertIn("Average Return", comparison.columns)
            self.assertIn("Return >= 20%", comparison.columns)


if __name__ == "__main__":
    unittest.main()
