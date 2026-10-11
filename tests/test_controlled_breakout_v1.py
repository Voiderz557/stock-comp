import unittest
from unittest.mock import patch

from strategies import controlled_breakout_v1 as strategy
from strategies.registry import (
    available_strategy_names,
    get_strategy,
    live_strategy_names,
    ml_feature_strategy_names,
)


def _base_result(volatility=0.02, price=110.0, ma50=100.0, signal="BUY", score=0.12):
    details = {
        "Price": price,
        "MA50": ma50,
        "Volatility 20D": volatility,
        "Breakout 20D": 0.01,
        "Final Score": score,
    }
    return {
        "Ticker": "TEST",
        "Score": score,
        "Signal": signal,
        "Reason": "base reason",
        "Factor Details": details,
        **details,
    }


class ControlledBreakoutV1Tests(unittest.TestCase):
    def test_registered_for_research_but_not_live_or_ml(self):
        self.assertIn("Controlled Breakout V1", available_strategy_names())
        self.assertNotIn("Controlled Breakout V1", live_strategy_names())
        self.assertNotIn("Controlled Breakout V1", ml_feature_strategy_names())
        self.assertIs(get_strategy("Controlled Breakout V1").analyze, strategy.analyze)

    def test_preregistered_constants_and_history(self):
        self.assertEqual(strategy.MAX_DAILY_VOLATILITY_20D, 0.035)
        self.assertEqual(strategy.MAX_MA50_EXTENSION, 0.20)
        self.assertEqual(strategy.RANKING_VOLATILITY_FLOOR, 0.01)
        self.assertGreaterEqual(strategy.REQUIRED_HISTORY_DAYS, 61)

    def test_low_risk_base_buy_stays_buy_and_uses_risk_adjusted_rank(self):
        with patch.object(strategy.breakout_volume_v1, "analyze", return_value=_base_result()):
            result = strategy.analyze("TEST", object())
        self.assertEqual(result["Signal"], "BUY")
        self.assertAlmostEqual(result["Score"], 6.0)

    def test_each_preregistered_guard_rejects_buy(self):
        cases = (
            _base_result(volatility=0.035),
            _base_result(price=120.0, ma50=100.0),
        )
        for base in cases:
            with self.subTest(base=base), patch.object(
                strategy.breakout_volume_v1, "analyze", return_value=base
            ):
                self.assertEqual(strategy.analyze("TEST", object())["Signal"], "WAIT")

    def test_guards_do_not_rewrite_non_buy_signal(self):
        base = _base_result(volatility=0.08, price=140, signal="AVOID")
        with patch.object(strategy.breakout_volume_v1, "analyze", return_value=base):
            result = strategy.analyze("TEST", object())
        self.assertEqual(result["Signal"], "AVOID")


if __name__ == "__main__":
    unittest.main()
