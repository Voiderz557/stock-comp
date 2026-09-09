import unittest

import pandas as pd

from market.regime import (
    DOWNTREND,
    HIGH_VOLATILITY,
    REQUIRED_HISTORY_DAYS,
    SIDEWAYS,
    STRONG_UPTREND,
    WEAK_UPTREND,
    classify_market_regime,
    compute_market_features,
)
from market.strategy_selector import (
    all_strategy_mappings,
    describe_availability,
    recommend_strategy,
)
from strategies.registry import available_strategy_names


def _price_frame(closes, start="2020-01-01"):
    return pd.DataFrame(
        {"Close": closes, "Open": closes, "Volume": [1_000] * len(closes)},
        index=pd.date_range(start, periods=len(closes)),
    )


def _linear_closes(length, start=100.0, slope=0.0):
    return [start + index * slope for index in range(length)]


def _flat_closes(length, start=100.0):
    return [start] * length


def _zigzag_closes(length, start=100.0, up=1.05, down=1.0 / 1.05):
    closes = [start]
    for index in range(1, length):
        factor = up if index % 2 == 1 else down
        closes.append(closes[-1] * factor)
    return closes


def _features(spy_closes, qqq_closes):
    return compute_market_features(_price_frame(spy_closes), _price_frame(qqq_closes))


class RequiredHistoryTests(unittest.TestCase):
    def test_required_history_is_at_least_200_days(self):
        self.assertGreaterEqual(REQUIRED_HISTORY_DAYS, 200)

    def test_insufficient_history_raises(self):
        short_closes = _linear_closes(REQUIRED_HISTORY_DAYS - 1, slope=0.5)
        with self.assertRaises(ValueError):
            _features(short_closes, short_closes)

    def test_exact_required_history_succeeds(self):
        closes = _linear_closes(REQUIRED_HISTORY_DAYS, slope=0.5)
        features = _features(closes, closes)
        self.assertIsNotNone(features)


class BullMarketTests(unittest.TestCase):
    def test_clear_bull_market_classifies_strong_uptrend(self):
        closes = _linear_closes(REQUIRED_HISTORY_DAYS + 20, start=100.0, slope=0.5)
        features = _features(closes, closes)
        result = classify_market_regime(features)
        self.assertEqual(result.regime, STRONG_UPTREND)
        self.assertGreater(result.confidence, 0.0)
        self.assertLess(features.market_volatility_20d, 0.02)
        self.assertGreater(features.spy_momentum_20d, 0)
        self.assertGreater(features.spy_momentum_60d, 0)


class BearMarketTests(unittest.TestCase):
    def test_clear_bear_market_classifies_downtrend(self):
        closes = _linear_closes(REQUIRED_HISTORY_DAYS + 20, start=300.0, slope=-0.5)
        features = _features(closes, closes)
        result = classify_market_regime(features)
        self.assertEqual(result.regime, DOWNTREND)
        self.assertLess(features.spy_momentum_20d, 0)
        self.assertLess(features.spy_momentum_60d, 0)


class FlatMarketTests(unittest.TestCase):
    def test_flat_market_classifies_sideways(self):
        closes = _flat_closes(REQUIRED_HISTORY_DAYS + 20, start=100.0)
        features = _features(closes, closes)
        result = classify_market_regime(features)
        self.assertEqual(result.regime, SIDEWAYS)
        self.assertAlmostEqual(features.spy_momentum_20d, 0.0)
        self.assertAlmostEqual(features.spy_momentum_60d, 0.0)


class HighVolatilityTests(unittest.TestCase):
    def test_extreme_daily_swings_classify_high_volatility(self):
        closes = _zigzag_closes(REQUIRED_HISTORY_DAYS + 20, start=100.0, up=1.06, down=1.0 / 1.06)
        features = _features(closes, closes)
        self.assertGreaterEqual(features.market_volatility_20d, 0.02)
        result = classify_market_regime(features)
        self.assertEqual(result.regime, HIGH_VOLATILITY)

    def test_high_volatility_overrides_bullish_trend(self):
        # Strong upward drift, but with large daily zigzags layered on top so
        # volatility stays high even though the underlying trend is bullish.
        base = _linear_closes(REQUIRED_HISTORY_DAYS + 20, start=100.0, slope=0.6)
        volatile = list(base)
        for index in range(1, len(volatile)):
            factor = 1.05 if index % 2 == 1 else 1.0 / 1.05
            volatile[index] = volatile[index] * factor
        features = _features(volatile, volatile)
        self.assertGreaterEqual(features.market_volatility_20d, 0.02)
        result = classify_market_regime(features)
        self.assertEqual(result.regime, HIGH_VOLATILITY)


class WeakUptrendTests(unittest.TestCase):
    def test_above_long_term_trend_with_flattening_momentum_is_weak_uptrend(self):
        # Rises steadily for a long time then flattens out over the most
        # recent 20 days at the same price. 20D momentum goes to ~0 (not
        # positive, so STRONG_UPTREND's "all momentum positive" requirement
        # fails) while 60D momentum stays clearly positive (so it is not
        # "low absolute momentum" either) and price stays comfortably above
        # both MA50 and MA200 (so it never qualifies as DOWNTREND).
        rising_part = _linear_closes(REQUIRED_HISTORY_DAYS - 20, start=100.0, slope=0.5)
        flat_part = _flat_closes(20, start=rising_part[-1])
        closes = rising_part + flat_part
        features = _features(closes, closes)

        self.assertAlmostEqual(features.spy_momentum_20d, 0.0)
        self.assertGreater(features.spy_momentum_60d, 0.02)
        self.assertGreater(features.spy_price, features.spy_ma50)
        self.assertGreater(features.spy_price, features.spy_ma200)

        result = classify_market_regime(features)
        self.assertEqual(result.regime, WEAK_UPTREND)


class StrategyMappingTests(unittest.TestCase):
    def test_all_regimes_have_a_mapping(self):
        mapping = all_strategy_mappings()
        for regime in (STRONG_UPTREND, WEAK_UPTREND, SIDEWAYS, DOWNTREND, HIGH_VOLATILITY):
            self.assertIn(regime, mapping)

    def test_strong_uptrend_mapping_matches_spec(self):
        recommendation = recommend_strategy(STRONG_UPTREND)
        self.assertEqual(
            recommendation.preferred_strategies,
            (
                "Aggressive Momentum V1",
                "Breakout Volume V1",
                "Relative Strength Momentum V1",
            ),
        )
        self.assertEqual(recommendation.strategies_to_avoid, ())

    def test_weak_uptrend_mapping_matches_spec(self):
        recommendation = recommend_strategy(WEAK_UPTREND)
        self.assertEqual(
            recommendation.preferred_strategies,
            ("Momentum V2", "Relative Strength Momentum V1"),
        )

    def test_sideways_mapping_uses_baseline_and_flags_future_work(self):
        recommendation = recommend_strategy(SIDEWAYS)
        self.assertEqual(recommendation.preferred_strategies, ("Baseline",))
        self.assertTrue(
            any("Mean Reversion" in note for note in recommendation.notes)
        )

    def test_downtrend_mapping_prefers_no_long_strategy(self):
        recommendation = recommend_strategy(DOWNTREND)
        self.assertEqual(recommendation.preferred_strategies, ())
        self.assertTrue(any("bearish" in note.lower() for note in recommendation.notes))

    def test_high_volatility_mapping_avoids_aggressive_momentum(self):
        recommendation = recommend_strategy(HIGH_VOLATILITY)
        self.assertIn("Momentum V2", recommendation.preferred_strategies)
        self.assertIn("Aggressive Momentum V1", recommendation.strategies_to_avoid)

    def test_recommend_strategy_accepts_regime_result(self):
        closes = _linear_closes(REQUIRED_HISTORY_DAYS + 20, start=100.0, slope=0.5)
        result = classify_market_regime(_features(closes, closes))
        recommendation = recommend_strategy(result)
        self.assertEqual(recommendation.regime, result.regime)

    def test_describe_availability_flags_unregistered_strategies(self):
        pairs = describe_availability(["Baseline", "Aggressive Momentum V1"])
        self.assertIn(("Baseline", True), pairs)
        self.assertIn(("Aggressive Momentum V1", False), pairs)


class ExistingStrategiesUnchangedTests(unittest.TestCase):
    def test_registry_still_only_contains_baseline_and_momentum_v2(self):
        self.assertEqual(available_strategy_names(), ["Baseline", "Momentum V2"])


if __name__ == "__main__":
    unittest.main()
