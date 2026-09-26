"""Adversarial checks added before large-scale historical evaluation."""
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from benchmarking.coverage import audit_price_coverage
from benchmarking.metrics import compute_aggregate_metrics, compute_daily_portfolio_risk
from benchmarking.promotion import _is_finite
from benchmarking.promotion import evaluate_promotion, RESEARCH_MORE
from tests.test_benchmarking_promotion import _metrics
from benchmarking.runner import run_benchmark
from tests.test_benchmarking_metrics import _period_row


class BenchmarkValidationSafetyTests(unittest.TestCase):
    def setUp(self):
        dates = pd.bdate_range("2023-01-02", "2023-03-31")
        frame = pd.DataFrame({"Open": 100.0, "Close": 100.0}, index=dates)
        self.prices = {ticker: frame.copy() for ticker in ("SPY", "QQQ", "AAPL")}

    def audit(self):
        return audit_price_coverage(self.prices, "2023-01-01", "2023-03-31", universe=["AAPL"])

    def test_complete_supplied_prices_pass(self):
        self.assertTrue(self.audit()["Coverage Is Valid"])

    def test_missing_supplied_constituent_fails(self):
        del self.prices["AAPL"]
        self.assertFalse(self.audit()["Coverage Is Valid"])

    def test_one_missing_internal_session_fails(self):
        self.prices["AAPL"] = self.prices["AAPL"].drop(self.prices["AAPL"].index[20])
        self.assertFalse(self.audit()["Coverage Is Valid"])

    def test_missing_context_fails(self):
        del self.prices["QQQ"]
        self.assertFalse(self.audit()["Coverage Is Valid"])

    def test_nan_infinite_and_zero_prices_fail(self):
        for value in (np.nan, np.inf, 0, -1):
            with self.subTest(value=value):
                self.prices["AAPL"].iloc[10, 0] = value
                self.assertFalse(self.audit()["Coverage Is Valid"])

    def test_membership_receives_observation_dates_only(self):
        with patch("benchmarking.coverage.get_membership_ranges", return_value={"AAPL": [(pd.Timestamp("2023-01-01"), pd.Timestamp("2023-03-31"))]}) as membership:
            audit_price_coverage(self.prices, "2023-01-01", "2023-03-31")
        membership.assert_called_once_with(pd.Timestamp("2023-01-01"), pd.Timestamp("2023-03-31"))

    def test_failed_method_invalidates_run(self):
        with patch("benchmarking.runner.run_portfolio_simulation", side_effect=RuntimeError("test failure")):
            result = run_benchmark("1 month", "2023-01-01", "2023-03-31", 1, 43,
                requested_strategies=("Baseline",), include_regime_switching=False,
                ml_models=(), universe=["AAPL"], price_data=self.prices)
        self.assertFalse(result.config["benchmark_valid"])
        self.assertTrue(result.errors)

    def test_overlap_is_not_compounded(self):
        metrics = compute_aggregate_metrics([_period_row(1, .2), _period_row(2, .2)])
        self.assertTrue(pd.isna(metrics["Total Return"]))
        self.assertTrue(pd.isna(metrics["Annualized Return"]))
        self.assertEqual(metrics["P(Return >= 20%)"], 1)

    def test_exact_equity_threshold_survives_floating_point_subtraction(self):
        metrics = compute_aggregate_metrics([_period_row(1, 120000 / 100000 - 1)])
        self.assertEqual(metrics["P(Return >= 20%)"], 1)
        below = compute_aggregate_metrics([_period_row(1, .199999)])
        self.assertEqual(below["P(Return >= 20%)"], 0)

    def test_nonoverlapping_periods_can_be_compounded(self):
        rows = [_period_row(1, .2), _period_row(2, .2)]
        rows[1]["Actual Start"] = pd.Timestamp("2020-03-01")
        rows[1]["Actual End"] = pd.Timestamp("2020-04-01")
        self.assertAlmostEqual(compute_aggregate_metrics(rows)["Total Return"], .44)

    def test_nonfinite_promotion_metrics_are_unavailable(self):
        for value in (None, np.nan, np.inf, -np.inf, "missing"):
            self.assertFalse(_is_finite(value))

    def test_overlapping_confirmation_cannot_promote(self):
        decision = evaluate_promotion("ML", _metrics(**{"Overlapping Periods": True}),
                                      _metrics(), "Baseline", [.1] * 20)
        self.assertEqual(decision.decision, RESEARCH_MORE)

    def test_daily_risk_uses_equity_returns_and_252_sessions(self):
        returns = np.array([.01, -.02, .03, -.01, .02, -.03])
        values = np.r_[100000, 100000 * np.cumprod(1 + returns)]
        history = [{"Date": date, "Portfolio Value": value} for date, value in
                   zip(pd.bdate_range("2023-01-02", periods=len(values)), values)]
        risk = compute_daily_portfolio_risk(history)
        self.assertAlmostEqual(risk["Daily Portfolio Volatility"], returns.std(ddof=1))
        self.assertAlmostEqual(risk["Annualized Portfolio Volatility"], returns.std(ddof=1) * np.sqrt(252))
        self.assertAlmostEqual(risk["Daily Downside Deviation"], np.sqrt(np.mean(np.minimum(returns, 0) ** 2)))

    def test_all_cash_has_zero_volatility_but_undefined_ratios(self):
        history = [{"Date": date, "Portfolio Value": 100000} for date in pd.bdate_range("2023-01-02", periods=10)]
        risk = compute_daily_portfolio_risk(history)
        self.assertEqual(risk["Daily Portfolio Volatility"], 0)
        self.assertTrue(pd.isna(risk["Annualized Portfolio Sharpe"]))
        self.assertTrue(pd.isna(risk["Annualized Portfolio Sortino"]))

    def test_invalid_equity_not_silently_filled(self):
        history = [{"Date": date, "Portfolio Value": value} for date, value in
                   zip(pd.bdate_range("2023-01-02", periods=3), [100000, np.nan, 110000])]
        self.assertTrue(pd.isna(compute_daily_portfolio_risk(history)["Daily Portfolio Volatility"]))


if __name__ == "__main__":
    unittest.main()
