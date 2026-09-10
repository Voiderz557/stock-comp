"""Integration tests for `benchmarking.runner` (and the ZIP export)."""

import unittest
import zipfile
import io
import json

from tests.benchmark_fixtures import (
    BENCHMARK_TICKERS,
    TEST_TRAINING_WINDOW_DAYS,
    benchmark_period_bounds,
    build_benchmark_price_data,
)

from benchmarking.export import EXPORT_FILENAMES, build_benchmark_zip
from benchmarking.promotion import INVALID, PROMOTE, REJECT, RESEARCH_MORE
from benchmarking.runner import REQUESTED_RULE_BASED_STRATEGIES, run_benchmark
from strategies.registry import get_strategy


class RunBenchmarkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.price_data = build_benchmark_price_data()
        cls.earliest_allowed, cls.latest_allowed = benchmark_period_bounds(cls.price_data)
        cls.result = run_benchmark(
            duration="1 month",
            earliest_allowed=cls.earliest_allowed,
            latest_allowed=cls.latest_allowed,
            number_of_tests=2,
            random_seed=11,
            requested_strategies=("Baseline", "Momentum V2", "Mean Reversion V1"),
            include_regime_switching=True,
            ml_models=("Logistic Regression",),
            top_n_values=(5, 10),
            universe=list(BENCHMARK_TICKERS),
            price_data=cls.price_data,
            training_window_days=TEST_TRAINING_WINDOW_DAYS,
        )

    def test_no_unavailable_strategies_when_only_available_ones_are_requested(self):
        self.assertEqual(self.result.unavailable_strategies, [])

    def test_all_requested_rule_based_strategies_are_registered(self):
        availability_result = run_benchmark(
            duration="1 month",
            earliest_allowed=self.earliest_allowed,
            latest_allowed=self.latest_allowed,
            number_of_tests=1,
            random_seed=1,
            requested_strategies=REQUESTED_RULE_BASED_STRATEGIES,
            include_regime_switching=False,
            ml_models=(),
            universe=list(BENCHMARK_TICKERS),
            price_data=self.price_data,
            training_window_days=TEST_TRAINING_WINDOW_DAYS,
        )
        self.assertEqual(availability_result.unavailable_strategies, [])

    def test_all_methods_are_evaluated_on_identical_periods(self):
        period_table = self.result.period_table
        self.assertFalse(period_table.empty)
        for test_number, group in period_table.groupby("Test"):
            self.assertEqual(group["Requested Start"].nunique(), 1)
            self.assertEqual(group["Requested End"].nunique(), 1)
            # Every method that produced a result for this test number must
            # share the exact same requested boundaries as every other.
            methods_seen = set(group["Method"])
            self.assertGreaterEqual(len(methods_seen), 1)

    def test_aggregate_table_covers_every_requested_method(self):
        methods = set(self.result.aggregate_table["Method"]) if not self.result.aggregate_table.empty else set()
        expected_present = {"Baseline", "Momentum V2", "Mean Reversion V1", "Regime Switching"}
        # ML training can legitimately fail to produce a fold for a given
        # tiny synthetic period, but the rule-based methods must always run.
        self.assertTrue(expected_present.issubset(methods), msg=f"Missing from {methods}")

    def test_leakage_audit_is_present_and_reports_a_boolean_validity(self):
        self.assertIn("Is Valid", self.result.leakage_audit)
        self.assertIsInstance(self.result.leakage_audit["Is Valid"], bool)
        self.assertGreaterEqual(len(self.result.leakage_audit["Checks"]), 5)

    def test_promotion_table_only_covers_ml_methods_and_uses_valid_decisions(self):
        if self.result.promotion_table.empty:
            self.skipTest("No ML fold trained successfully for this tiny synthetic run.")
        self.assertTrue(
            self.result.promotion_table["Decision"]
            .isin([PROMOTE, RESEARCH_MORE, REJECT, INVALID])
            .all()
        )
        for method in self.result.promotion_table["Method"]:
            self.assertTrue(method.startswith("Logistic Regression Top"))

    def test_failed_leakage_audit_forces_every_promotion_to_reject(self):
        # Directly exercise the "failed audit invalidates the benchmark"
        # requirement using the runner's own promotion wiring: re-evaluate
        # promotion with the SAME metrics but leakage_audit_is_valid=False.
        from benchmarking.promotion import evaluate_promotion

        if self.result.aggregate_table.empty:
            self.skipTest("No aggregate results for this tiny synthetic run.")
        ml_rows = self.result.aggregate_table[
            self.result.aggregate_table["Method"].str.startswith("Logistic Regression")
        ]
        if ml_rows.empty:
            self.skipTest("No ML aggregate row produced for this tiny synthetic run.")
        ml_metrics = ml_rows.iloc[0].to_dict()
        result = evaluate_promotion(
            "Logistic Regression Top 5", ml_metrics, {}, "Baseline", [0.5, 0.5], leakage_audit_is_valid=False
        )
        self.assertEqual(result.decision, INVALID)
        self.assertNotEqual(result.decision, PROMOTE)

    def test_existing_strategy_analyze_contract_is_unaffected_by_benchmarking(self):
        ticker = BENCHMARK_TICKERS[0]
        historical = self.price_data[ticker].iloc[:400]
        result = get_strategy("Baseline").analyze(ticker, historical)
        for key in ("Ticker", "Score", "Signal", "Reason", "Factor Details"):
            self.assertIn(key, result)

    def test_zip_export_contains_every_required_file(self):
        zip_bytes = build_benchmark_zip(self.result)
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
            names = set(archive.namelist())
            self.assertEqual(names, set(EXPORT_FILENAMES))
            config = json.loads(archive.read("benchmark_config.json"))
            self.assertEqual(config["number_of_tests"], 2)


if __name__ == "__main__":
    unittest.main()
