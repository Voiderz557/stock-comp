import unittest

import pandas as pd

from market.regime import SIDEWAYS
from market.strategy_selector import recommend_strategy
from strategies.mean_reversion_v1 import (
    REQUIRED_HISTORY_DAYS,
    calculate_rsi_oversold_score,
)
from strategies.registry import available_strategy_names, get_strategy


def _price_frame(closes, start="2020-01-01"):
    return pd.DataFrame(
        {"Close": closes, "Open": closes, "Volume": [1_000] * len(closes)},
        index=pd.date_range(start, periods=len(closes)),
    )


def _linear_closes(length, slope=0.5, start=100.0):
    return [start + index * slope for index in range(length)]


def _build_series(uptrend_days, uptrend_slope, start, recent_returns):
    """A long linear uptrend followed by a hand-tuned "recent" return path.

    `recent_returns` is a list of daily percentage returns applied in order
    after the uptrend, letting each test precisely control the resulting
    price/MA20/MA50/MA200/RSI14/momentum combination.
    """
    closes = [start + index * uptrend_slope for index in range(uptrend_days)]
    last = closes[-1]
    for pct in recent_returns:
        last = last * (1 + pct)
        closes.append(last)
    return closes


def _analyze(ticker, closes):
    return get_strategy("Mean Reversion V1").analyze(ticker, _price_frame(closes))


# A 20-day "recent" return path, applied after a 200-day uptrend, that lands
# RSI14 in the 25-45 BUY band, keeps 5-day momentum near zero, and keeps
# 20-day momentum only mildly negative - a textbook healthy pullback.
_HEALTHY_PULLBACK_RETURNS = [
    -0.010, 0.003, -0.010, 0.003, -0.010, 0.003,
    -0.010, 0.003, -0.010, 0.003, -0.008, 0.003,
    -0.006, 0.002,
    0.001, -0.001, 0.001, -0.001, 0.0005, -0.0005,
]

# A long, uninterrupted decline steep enough to drop price below its MA200 -
# a genuine breakdown, not a pullback.
_DEEP_DECLINE_BELOW_MA200_RETURNS = [-0.02] * 40

# A strong, uninterrupted downtrend with severely negative 20-day momentum.
_STRONG_DOWNTREND_RETURNS = [-0.015] * 25


class RegistrationTests(unittest.TestCase):
    def test_registers_with_required_history_days(self):
        self.assertIn("Mean Reversion V1", available_strategy_names())
        definition = get_strategy("Mean Reversion V1")
        self.assertEqual(definition.required_history_days, REQUIRED_HISTORY_DAYS)

    def test_required_history_covers_ma200_rsi_ma50_and_momentum(self):
        # MA200 is the longest lookback any factor needs.
        self.assertGreaterEqual(REQUIRED_HISTORY_DAYS, 200)

    def test_insufficient_history_returns_none(self):
        closes = _linear_closes(REQUIRED_HISTORY_DAYS - 1, slope=0.3)
        self.assertIsNone(_analyze("SHORT", closes))

    def test_exact_required_history_produces_a_result(self):
        closes = _linear_closes(REQUIRED_HISTORY_DAYS, slope=0.3)
        self.assertIsNotNone(_analyze("EXACT", closes))

    def test_result_has_the_standard_fields(self):
        closes = _build_series(200, 0.3, 100.0, _HEALTHY_PULLBACK_RETURNS)
        result = _analyze("BUY_CASE", closes)
        self.assertIsNotNone(result)
        self.assertTrue(
            {"Ticker", "Score", "Signal", "Reason", "Factor Details"} <= result.keys()
        )
        for key in (
            "Price",
            "MA20",
            "MA50",
            "MA200",
            "Distance from MA20",
            "Distance from MA50",
            "RSI14",
            "Momentum 5D",
            "Momentum 20D",
            "Volatility 20D",
            "Above MA200",
            "Raw Score",
            "Volatility Penalty",
            "Final Score",
        ):
            self.assertIn(key, result["Factor Details"])


class BuySignalTests(unittest.TestCase):
    def test_healthy_pullback_above_ma200_produces_buy(self):
        closes = _build_series(200, 0.3, 100.0, _HEALTHY_PULLBACK_RETURNS)
        result = _analyze("PULLBACK", closes)
        self.assertIsNotNone(result)
        self.assertTrue(result["Above MA200"])
        self.assertLess(result["Price"], result["MA20"])
        self.assertTrue(25.0 <= result["RSI14"] <= 45.0)
        self.assertEqual(result["Signal"], "BUY")

    def test_deeply_oversold_below_ma200_does_not_buy(self):
        closes = _build_series(200, 0.3, 100.0, _DEEP_DECLINE_BELOW_MA200_RETURNS)
        result = _analyze("BELOW_MA200", closes)
        self.assertIsNotNone(result)
        self.assertFalse(result["Above MA200"])
        # Deeply oversold (very low RSI) but below MA200 must not be a BUY.
        self.assertLessEqual(result["RSI14"], 45.0)
        self.assertNotEqual(result["Signal"], "BUY")
        self.assertEqual(result["Signal"], "AVOID")

    def test_strong_downtrend_produces_avoid(self):
        closes = _build_series(200, 0.3, 100.0, _STRONG_DOWNTREND_RETURNS)
        result = _analyze("DOWNTREND", closes)
        self.assertIsNotNone(result)
        self.assertLessEqual(result["Momentum 20D"], -0.20)
        self.assertEqual(result["Signal"], "AVOID")


class ScoringTests(unittest.TestCase):
    def test_lower_rsi_within_reasonable_range_improves_score(self):
        higher_rsi_score = calculate_rsi_oversold_score(40.0)
        lower_rsi_score = calculate_rsi_oversold_score(30.0)
        self.assertGreater(lower_rsi_score, higher_rsi_score)

    def test_lower_rsi_increases_full_strategy_score_with_other_factors_held_equal(self):
        # Two otherwise-identical healthy pullbacks; the only lever we pull is
        # how oversold the final RSI reading is by nudging the very last
        # day's return (which only affects the newest RSI delta and the
        # current price, not any moving-average window boundary).
        mild_dip_returns = _HEALTHY_PULLBACK_RETURNS[:-1] + [-0.001]
        deeper_dip_returns = _HEALTHY_PULLBACK_RETURNS[:-1] + [-0.02]

        mild = _analyze("MILD", _build_series(200, 0.3, 100.0, mild_dip_returns))
        deeper = _analyze("DEEPER", _build_series(200, 0.3, 100.0, deeper_dip_returns))
        self.assertIsNotNone(mild)
        self.assertIsNotNone(deeper)
        self.assertLess(deeper["RSI14"], mild["RSI14"])
        self.assertGreater(deeper["Score"], mild["Score"])

    def test_extreme_volatility_reduces_score(self):
        # 210 linear closes; perturb a zero-sum block at indices -20..-17 so
        # every moving average (MA20/50/200, all linear) is unaffected, and
        # both momentum anchors (-1, -6, -21) plus the entire RSI window
        # (last 15 closes) are left untouched - isolating volatility alone.
        base_closes = _linear_closes(210, slope=0.4, start=100.0)
        calm_frame = _price_frame(list(base_closes))

        volatile_closes = list(base_closes)
        amplitude = 5.0
        perturb_indices = [
            len(volatile_closes) - 20,
            len(volatile_closes) - 19,
            len(volatile_closes) - 18,
            len(volatile_closes) - 17,
        ]
        signs = [1, -1, 1, -1]
        for position, sign in zip(perturb_indices, signs):
            volatile_closes[position] = base_closes[position] + sign * amplitude
        volatile_frame = _price_frame(volatile_closes)

        calm_result = get_strategy("Mean Reversion V1").analyze("CALM", calm_frame)
        volatile_result = get_strategy("Mean Reversion V1").analyze("VOL", volatile_frame)

        self.assertIsNotNone(calm_result)
        self.assertIsNotNone(volatile_result)
        for key in (
            "MA20",
            "MA50",
            "MA200",
            "RSI14",
            "Momentum 5D",
            "Momentum 20D",
            "Raw Score",
        ):
            self.assertAlmostEqual(calm_result[key], volatile_result[key])
        self.assertGreater(volatile_result["Volatility 20D"], calm_result["Volatility 20D"])
        self.assertGreater(
            volatile_result["Volatility Penalty"], calm_result["Volatility Penalty"]
        )
        self.assertLess(volatile_result["Final Score"], calm_result["Final Score"])

    def test_calculations_use_only_the_rows_it_was_given(self):
        """analyze() must not depend on anything beyond its input DataFrame."""
        shared_prefix = _linear_closes(REQUIRED_HISTORY_DAYS, slope=0.3)
        continued_calm = shared_prefix + _linear_closes(
            5, slope=0.3, start=shared_prefix[-1] + 0.3
        )
        continued_spike = shared_prefix + [shared_prefix[-1] * 5] * 5

        calm_result = get_strategy("Mean Reversion V1").analyze(
            "CALM", _price_frame(continued_calm).iloc[: REQUIRED_HISTORY_DAYS]
        )
        spike_result = get_strategy("Mean Reversion V1").analyze(
            "SPIKE", _price_frame(continued_spike).iloc[: REQUIRED_HISTORY_DAYS]
        )
        self.assertIsNotNone(calm_result)
        self.assertIsNotNone(spike_result)
        for key in (
            "MA20",
            "MA50",
            "MA200",
            "RSI14",
            "Momentum 5D",
            "Momentum 20D",
            "Volatility 20D",
            "Final Score",
        ):
            self.assertAlmostEqual(calm_result[key], spike_result[key])


class RegimeSelectorTests(unittest.TestCase):
    def test_sideways_regime_prefers_mean_reversion_v1(self):
        recommendation = recommend_strategy(SIDEWAYS)
        self.assertEqual(recommendation.preferred_strategies[0], "Mean Reversion V1")
        self.assertIn("Mean Reversion V1", recommendation.preferred_strategies)


if __name__ == "__main__":
    unittest.main()
