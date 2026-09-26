"""Cross-strategy scenarios and real account-to-plan integration (temporary DB)."""
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from paper_trading import storage
from paper_trading.account_setup import preview_account_snapshot, apply_account_snapshot
from paper_trading.plan_store import save_plan, load_latest_plan
from paper_trading.trading_plan import build_trading_plan
from strategies.registry import available_strategy_names, get_strategy, invoke_analyze
from strategies import aggressive_momentum_v1, breakout_volume_v1


def frame(start=100, end=200, rows=260):
    closes = np.linspace(start, end, rows)
    return pd.DataFrame({"Open": closes, "Close": closes, "Volume": 1000000.0},
                        index=pd.bdate_range("2025-01-02", periods=rows))


class StrategyScenarioTests(unittest.TestCase):
    def analyze(self, name, prices):
        strategy = get_strategy(name)
        return invoke_analyze(strategy.analyze, "TEST", prices, frame(100, 100))

    def test_momentum_and_breakout_buy_clean_uptrend(self):
        for name in available_strategy_names():
            if name == "Mean Reversion V1":
                continue
            with self.subTest(strategy=name):
                result = self.analyze(name, frame())
                self.assertEqual(result["Signal"], "BUY")
                self.assertTrue(np.isfinite(result["Score"]))
                self.assertTrue(result["Reason"])

    def test_all_strategies_refuse_bear_and_flat_buys(self):
        for name in available_strategy_names():
            for prices in (frame(200, 100), frame(100, 100)):
                with self.subTest(strategy=name, final=prices.Close.iloc[-1]):
                    self.assertNotEqual(self.analyze(name, prices)["Signal"], "BUY")

    def test_all_strategies_require_declared_history(self):
        for name in available_strategy_names():
            with self.subTest(strategy=name):
                required = get_strategy(name).required_history_days
                self.assertIsNone(self.analyze(name, frame().iloc[:required-1]))
                self.assertIsNotNone(self.analyze(name, frame().iloc[:required]))

    def test_relative_strength_score_falls_with_stronger_benchmark(self):
        definition = get_strategy("Relative Strength Momentum V1")
        weak = invoke_analyze(definition.analyze, "TEST", frame(), frame(100, 110))
        strong = invoke_analyze(definition.analyze, "TEST", frame(), frame(100, 180))
        self.assertGreater(weak["Score"], strong["Score"])

    def test_breakout_excludes_today_and_volume_improves_both_scores(self):
        for module in (aggressive_momentum_v1, breakout_volume_v1):
            with self.subTest(module=module.__name__):
                prices = frame()
                normal = module.analyze("TEST", prices)
                prices.loc[prices.index[-5:], "Volume"] *= 3
                higher_volume = module.analyze("TEST", prices)
                self.assertGreater(higher_volume["Score"], normal["Score"])
        closes = pd.Series([100.] * 60 + [120.])
        self.assertEqual(breakout_volume_v1.calculate_prior_high(closes, 60), 100)

    def test_volatility_penalty_reduces_score_with_other_factors_fixed(self):
        for module in (aggressive_momentum_v1, breakout_volume_v1):
            with self.subTest(module=module.__name__):
                with patch.object(module, "calculate_volatility", return_value=.01):
                    lower = module.analyze("TEST", frame())
                with patch.object(module, "calculate_volatility", return_value=.05):
                    higher = module.analyze("TEST", frame())
                self.assertGreater(lower["Score"], higher["Score"])


class RealPlanPersistenceTests(unittest.TestCase):
    def test_manual_entry_restart_actual_strategy_plan_and_reload(self):
        with TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            db = Path(directory) / "test.sqlite3"
            storage.initialize_storage(db)
            preview = preview_account_snapshot(100000, 80000, [
                {"ticker": "AAPL", "direction": "LONG", "quantity": 100, "average_price": 200}
            ], "2026-01-01", db_path=db)
            self.assertTrue(preview["can_save"])
            apply_account_snapshot(preview, db_path=db)
            # A new process reads the persisted account; no live database used.
            command = "import json,sys; from paper_trading import storage; print(json.dumps(storage.get_account_state(sys.argv[1])))"
            reopened = subprocess.run([sys.executable, "-c", command, str(db)], capture_output=True, text=True, check=True, timeout=30)
            account = json.loads(reopened.stdout)
            self.assertEqual(account["Cash"], 80000)
            positions = storage.get_open_positions(db)
            plan = build_trading_plan("Aggressive Momentum V1", account, positions,
                                      {"AAPL": frame(), "MSFT": frame(50, 100)},
                                      ["AAPL", "MSFT", "MISSING"])
            self.assertIn("MISSING", plan["unavailable_universe"])
            self.assertTrue(any(row["Ticker"] == "MSFT" for row in plan["buys"]))
            self.assertGreaterEqual(plan["cash_after"], 0)
            self.assertEqual(plan["holdings"][0]["ticker"], "AAPL")
            self.assertTrue(plan["buys"][0]["Reason"])
            save_plan(plan, account, positions, db_path=db)
            saved = load_latest_plan(db)
            self.assertEqual(saved["strategy_name"], "Aggressive Momentum V1")
            self.assertEqual(saved["buys"][0]["Ticker"], plan["buys"][0]["Ticker"])
            self.assertEqual(storage.get_account_state(db)["Cash"], 80000)
            self.assertEqual(len(storage.get_open_positions(db)), 1)


if __name__ == "__main__":
    unittest.main()
