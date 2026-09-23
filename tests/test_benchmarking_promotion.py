"""Tests for `benchmarking.promotion`."""

import unittest

from benchmarking.promotion import (
    INVALID,
    PROMOTE,
    REJECT,
    RESEARCH_MORE,
    evaluate_promotion,
)


def _metrics(**overrides):
    base = {
        "Median Excess Return": 0.02,
        "Beat SPY %": 0.60,
        "P(Return >= 20%)": 0.40,
        "Sharpe Ratio": 1.0,
        "Worst Period Return": -0.05,
        "Periods Tested": 20,
    }
    base.update(overrides)
    return base


class PromotionGateTests(unittest.TestCase):
    def test_promote_when_every_criterion_passes(self):
        ml_metrics = _metrics()
        existing_metrics = _metrics(
            **{"P(Return >= 20%)": 0.30, "Sharpe Ratio": 0.5, "Worst Period Return": -0.06}
        )
        result = evaluate_promotion(
            "Logistic Regression Top 10",
            ml_metrics,
            existing_metrics,
            "Baseline",
            [0.05, 0.10, 0.15, 0.20, -0.02] * 4,
        )
        self.assertEqual(result.decision, PROMOTE)
        self.assertTrue(all(result.criteria.values()))

    def test_reject_when_clearly_underperforming(self):
        ml_metrics = _metrics(
            **{"Median Excess Return": -0.05, "Beat SPY %": 0.20, "P(Return >= 20%)": 0.0}
        )
        existing_metrics = _metrics()
        result = evaluate_promotion(
            "Random Forest Top 5",
            ml_metrics,
            existing_metrics,
            "Momentum V2",
            [-0.10] * 20,
        )
        self.assertEqual(result.decision, REJECT)

    def test_research_more_for_a_borderline_case(self):
        # Positive median excess and a decent beat rate, but not enough
        # periods tested to justify a full PROMOTE.
        ml_metrics = _metrics(**{"Periods Tested": 3})
        existing_metrics = _metrics()
        result = evaluate_promotion(
            "Logistic Regression Top 20",
            ml_metrics,
            existing_metrics,
            "Baseline",
            [0.05, 0.10, -0.02],
        )
        self.assertEqual(result.decision, RESEARCH_MORE)

    def test_failed_leakage_audit_forces_invalid_regardless_of_metrics(self):
        ml_metrics = _metrics()
        existing_metrics = _metrics()
        result = evaluate_promotion(
            "Logistic Regression Top 10",
            ml_metrics,
            existing_metrics,
            "Baseline",
            [0.2] * 20,
            leakage_audit_is_valid=False,
        )
        self.assertEqual(result.decision, INVALID)
        self.assertNotEqual(result.decision, PROMOTE)
        self.assertIn("Leakage audit FAILED", result.reasons[0])

    def test_missing_required_coverage_forces_invalid(self):
        result = evaluate_promotion(
            "Logistic Regression Top 10",
            _metrics(),
            _metrics(),
            "Baseline",
            [0.2] * 20,
            coverage_is_valid=False,
        )
        self.assertEqual(result.decision, INVALID)
        self.assertNotEqual(result.decision, PROMOTE)
        self.assertTrue(any("coverage" in reason.lower() for reason in result.reasons))

    def test_missing_metrics_are_not_treated_as_zero(self):
        result = evaluate_promotion(
            "Logistic Regression Top 5",
            {"Periods Tested": 20},
            _metrics(),
            "Baseline",
            [0.1] * 20,
        )
        self.assertNotEqual(result.decision, PROMOTE)
        self.assertEqual(result.decision, RESEARCH_MORE)
        self.assertTrue(any("unavailable" in reason.lower() for reason in result.reasons))

    def test_no_single_fold_dominance_check_flags_concentration(self):
        # One enormous winning period out of many small losers - even
        # though beat rate/median excess pass, concentration should fail.
        ml_metrics = _metrics(**{"Median Excess Return": 0.01, "Beat SPY %": 0.6})
        existing_metrics = _metrics()
        period_returns = [-0.01] * 19 + [5.0]
        result = evaluate_promotion(
            "Logistic Regression Top 5",
            ml_metrics,
            existing_metrics,
            "Baseline",
            period_returns,
        )
        self.assertFalse(
            result.criteria["No single period contributes > 50% of total positive return"]
        )
        self.assertNotEqual(result.decision, PROMOTE)

    def test_no_results_does_not_promote(self):
        result = evaluate_promotion("Logistic Regression Top 5", {}, {}, "Baseline", [])
        self.assertNotEqual(result.decision, PROMOTE)
        self.assertEqual(result.decision, RESEARCH_MORE)


if __name__ == "__main__":
    unittest.main()
