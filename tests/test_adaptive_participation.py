import unittest
from types import SimpleNamespace

from benchmarking.simulation import run_portfolio_simulation
from benchmarking.participation import (
    ADAPTIVE_PARTICIPATION_METHOD_NAME,
    FULL_POSITION_COUNT,
    HALF_POSITION_COUNT,
    AdaptiveParticipationStrategy,
    InvalidParticipationDataError,
    decide_participation,
)
from benchmarking.runner import REQUESTED_RULE_BASED_STRATEGIES
from tests.benchmark_fixtures import BENCHMARK_TICKERS, benchmark_period_bounds, build_benchmark_price_data
from market.regime import (
    DOWNTREND,
    HIGH_VOLATILITY,
    SIDEWAYS,
    STRONG_UPTREND,
    WEAK_UPTREND,
)
from strategies.registry import available_strategy_names, live_strategy_names


def _regime(name, *, spy_above=True, qqq_above=True):
    return SimpleNamespace(
        regime=name,
        features=SimpleNamespace(
            spy_price=110.0 if spy_above else 90.0,
            spy_ma200=100.0,
            qqq_price=110.0 if qqq_above else 90.0,
            qqq_ma200=100.0,
        ),
    )


def _breadth(above_ma50=0.70, positive_momentum=0.70, coverage=1.0):
    return SimpleNamespace(
        coverage_ratio=coverage,
        pct_above_ma50=above_ma50,
        pct_positive_momentum_20d=positive_momentum,
    )


class AdaptiveParticipationDecisionTests(unittest.TestCase):
    def test_full_requires_broad_non_defensive_market(self):
        decision = decide_participation(_regime(STRONG_UPTREND), _breadth())
        self.assertTrue(decision.valid)
        self.assertEqual(decision.state, "FULL")
        self.assertEqual(decision.top_n, FULL_POSITION_COUNT)

    def test_downtrend_or_weak_breadth_turns_participation_off(self):
        downtrend = decide_participation(_regime(DOWNTREND), _breadth())
        weak_breadth = decide_participation(
            _regime(WEAK_UPTREND), _breadth(0.39, 0.39)
        )
        self.assertEqual((downtrend.state, downtrend.top_n), ("OFF", 0))
        self.assertEqual((weak_breadth.state, weak_breadth.top_n), ("OFF", 0))

    def test_defensive_or_mixed_conditions_use_half_participation(self):
        cases = (
            (_regime(HIGH_VOLATILITY), _breadth()),
            (_regime(SIDEWAYS), _breadth()),
            (_regime(WEAK_UPTREND, spy_above=False), _breadth()),
            (_regime(WEAK_UPTREND), _breadth(0.59, 0.80)),
        )
        for regime, breadth in cases:
            with self.subTest(regime=regime.regime):
                decision = decide_participation(regime, breadth)
                self.assertEqual(decision.state, "HALF")
                self.assertEqual(decision.top_n, HALF_POSITION_COUNT)

    def test_exact_breadth_boundaries_are_inclusive(self):
        off_boundary = decide_participation(
            _regime(WEAK_UPTREND), _breadth(0.40, 0.40)
        )
        full_boundary = decide_participation(
            _regime(WEAK_UPTREND), _breadth(0.60, 0.60)
        )
        self.assertEqual(off_boundary.state, "HALF")
        self.assertEqual(full_boundary.state, "FULL")

    def test_incomplete_inputs_are_invalid_not_a_cash_timing_result(self):
        decision = decide_participation(
            _regime(STRONG_UPTREND), _breadth(coverage=0.79)
        )
        self.assertFalse(decision.valid)
        self.assertEqual(decision.state, "INVALID")
        strategy = AdaptiveParticipationStrategy()
        with self.assertRaises(InvalidParticipationDataError):
            strategy.select_candidates(
                [],
                regime_result=_regime(STRONG_UPTREND),
                breadth=_breadth(coverage=0.79),
                rebalance_date="2024-01-05",
            )

    def test_candidate_selection_reuses_controlled_breakout_order(self):
        candidates = [{"Ticker": str(index)} for index in range(8)]
        strategy = AdaptiveParticipationStrategy()
        selected, decision = strategy.select_candidates(
            candidates,
            regime_result=_regime(STRONG_UPTREND),
            breadth=_breadth(),
            rebalance_date="2024-01-05",
        )
        self.assertEqual(selected, candidates[:5])
        self.assertEqual(decision.state, "FULL")
        self.assertEqual(strategy.base_strategy.name, "Controlled Breakout V1")

    def test_research_method_is_benchmark_visible_but_not_live_registered(self):
        self.assertIn(ADAPTIVE_PARTICIPATION_METHOD_NAME, REQUESTED_RULE_BASED_STRATEGIES)
        self.assertNotIn(ADAPTIVE_PARTICIPATION_METHOD_NAME, available_strategy_names())
        self.assertNotIn(ADAPTIVE_PARTICIPATION_METHOD_NAME, live_strategy_names())

    def test_simulation_records_participation_and_respects_position_limit(self):
        price_data = build_benchmark_price_data()
        start, _ = benchmark_period_bounds(price_data)
        end = price_data["SPY"].index[
            price_data["SPY"].index.get_indexer([start])[0] + 12
        ]
        result = run_portfolio_simulation(
            AdaptiveParticipationStrategy(),
            start,
            end,
            price_data,
            universe=list(BENCHMARK_TICKERS),
        )

        self.assertTrue(result["Regime Path"])
        self.assertTrue(result["Exposure History"])
        for row in result["Regime Path"]:
            self.assertIn(row["Participation State"], {"OFF", "HALF", "FULL"})
            self.assertIn(row["Participation Top N"], {0, 2, 5})
        for row in result["Exposure History"]:
            self.assertLessEqual(row["Holdings Count"], row["Participation Top N"])


if __name__ == "__main__":
    unittest.main()
