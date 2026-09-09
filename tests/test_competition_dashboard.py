import unittest

from app.competition_recommender import (
    allocate_longs,
    allocate_shorts,
    build_paper_portfolio,
    build_recommendation_row,
    combined_long_score,
    combined_short_score,
    recommend_action,
)


def _baseline_result(signal, score):
    return {"Ticker": "T", "Signal": signal, "Score": score}


def _momentum_v2_result(signal, score):
    return {"Ticker": "T", "Signal": signal, "Score": score}


def _aggressive_result(signal, score, reason="synthetic"):
    return {"Ticker": "T", "Signal": signal, "Score": score, "Reason": reason}


class CombinedScoreTests(unittest.TestCase):
    def test_baseline_avoid_never_creates_short_evidence(self):
        baseline = _baseline_result("AVOID", 0)
        self.assertEqual(combined_long_score(baseline, None, None), 0.0)
        self.assertEqual(combined_short_score(None), 0.0)
        self.assertEqual(recommend_action(baseline, None, None), "WATCH")

    def test_short_requires_aggressive_bearish_evidence(self):
        baseline = _baseline_result("AVOID", 0)
        # Even with a strongly negative signal, without an Aggressive SHORT
        # result there is no SHORT evidence at all.
        self.assertEqual(recommend_action(baseline, None, None), "WATCH")

        aggressive_short = _aggressive_result("SHORT", -0.10)
        self.assertEqual(
            recommend_action(baseline, None, aggressive_short), "SHORT"
        )
        self.assertGreater(combined_short_score(aggressive_short), 0.0)

    def test_baseline_buy_contributes_to_long_only(self):
        baseline = _baseline_result("BUY", 3)
        self.assertAlmostEqual(combined_long_score(baseline, None, None), 1.0)
        self.assertEqual(combined_short_score(None), 0.0)
        self.assertEqual(recommend_action(baseline, None, None), "LONG")

    def test_aggressive_long_signal_drives_long_action(self):
        aggressive = _aggressive_result("LONG", 0.08)
        self.assertEqual(recommend_action(None, None, aggressive), "LONG")


class RecommendationRowTests(unittest.TestCase):
    def test_row_reports_expected_columns(self):
        row = build_recommendation_row(
            "AAPL",
            200.0,
            _baseline_result("BUY", 3),
            _momentum_v2_result("BUY", 0.05),
            _aggressive_result("LONG", 0.10),
        )
        expected_keys = {
            "Ticker",
            "Current Price",
            "Baseline Signal",
            "Baseline Score",
            "Momentum V2 Signal",
            "Momentum V2 Score",
            "Aggressive Signal",
            "Aggressive Score",
            "Combined Long Score",
            "Combined Short Score",
            "Recommended Action",
            "Reason",
        }
        self.assertEqual(set(row.keys()), expected_keys)
        self.assertEqual(row["Recommended Action"], "LONG")


class LongAllocationTests(unittest.TestCase):
    def test_long_allocation_never_exceeds_cap_per_stock(self):
        candidates = [
            {"Ticker": "A", "Combined Long Score": 3.0, "Reason": "r"},
            {"Ticker": "B", "Combined Long Score": 2.0, "Reason": "r"},
        ]
        allocations, remaining_capacity = allocate_longs(
            candidates, available_capacity=100_000.0, max_position_value=20_000.0
        )
        for item in allocations:
            self.assertLessEqual(item["Allocation"], 20_000.0)
        self.assertEqual([item["Ticker"] for item in allocations], ["A", "B"])
        self.assertEqual(remaining_capacity, 100_000.0 - 40_000.0)

    def test_weak_signals_leave_capacity_unused(self):
        allocations, remaining_capacity = allocate_longs(
            [], available_capacity=100_000.0, max_position_value=20_000.0
        )
        self.assertEqual(allocations, [])
        self.assertEqual(remaining_capacity, 100_000.0)

    def test_long_capacity_bounded_by_available_capacity_not_by_number_of_candidates(self):
        # Six strong candidates but only enough capacity for five full $20k slots.
        candidates = [
            {"Ticker": f"T{i}", "Combined Long Score": 6 - i, "Reason": "r"}
            for i in range(6)
        ]
        allocations, remaining_capacity = allocate_longs(
            candidates, available_capacity=100_000.0, max_position_value=20_000.0
        )
        self.assertEqual(len(allocations), 5)
        self.assertEqual(remaining_capacity, 0.0)
        for item in allocations:
            self.assertLessEqual(item["Allocation"], 20_000.0)


class ShortAllocationTests(unittest.TestCase):
    def test_short_allocation_never_exceeds_per_stock_cap(self):
        candidates = [
            {"Ticker": "A", "Combined Short Score": 5.0, "Reason": "r"},
            {"Ticker": "B", "Combined Short Score": 4.0, "Reason": "r"},
        ]
        allocations, _ = allocate_shorts(
            candidates,
            allow_shorts=True,
            available_capacity=40_000.0,
            max_position_value=20_000.0,
        )
        for item in allocations:
            self.assertLessEqual(item["Exposure"], 20_000.0)

    def test_short_exposure_never_exceeds_the_capacity_it_was_given(self):
        candidates = [
            {"Ticker": f"T{i}", "Combined Short Score": 10 - i, "Reason": "r"}
            for i in range(5)
        ]
        allocations, _ = allocate_shorts(
            candidates,
            allow_shorts=True,
            available_capacity=40_000.0,
            max_position_value=20_000.0,
        )
        total_exposure = sum(item["Exposure"] for item in allocations)
        self.assertLessEqual(total_exposure, 40_000.0)
        self.assertEqual(total_exposure, 40_000.0)
        self.assertEqual(len(allocations), 2)

    def test_shorts_disabled_produces_zero_exposure(self):
        candidates = [
            {"Ticker": "A", "Combined Short Score": 5.0, "Reason": "r"},
        ]
        allocations, _ = allocate_shorts(
            candidates,
            allow_shorts=False,
            available_capacity=40_000.0,
            max_position_value=20_000.0,
        )
        self.assertEqual(allocations, [])

    def test_no_bearish_candidates_means_no_forced_shorts(self):
        allocations, _ = allocate_shorts(
            [], allow_shorts=True, available_capacity=40_000.0, max_position_value=20_000.0
        )
        self.assertEqual(allocations, [])

    def test_shorts_receive_zero_capacity_when_longs_already_used_it_all(self):
        """Longs are allocated first from the SHARED pool; if they consume
        all of it, shorts get nothing left - not an independent budget."""
        candidates = [
            {"Ticker": "A", "Combined Short Score": 5.0, "Reason": "r"},
        ]
        allocations, remaining = allocate_shorts(
            candidates,
            allow_shorts=True,
            available_capacity=0.0,  # longs already used the whole shared pool
            max_position_value=20_000.0,
        )
        self.assertEqual(allocations, [])
        self.assertEqual(remaining, 0.0)


class SharedCapacityPortfolioTests(unittest.TestCase):
    """LONG and SHORT must draw from ONE shared gross-exposure pool, not two
    separate wallets - see the user-provided examples this suite proves."""

    def _rows(self, long_price=100.0, short_price=50.0):
        return [
            {
                "Ticker": "LONGCO",
                "Current Price": long_price,
                "Recommended Action": "LONG",
                "Combined Long Score": 1.0,
                "Combined Short Score": 0.0,
                "Reason": "long evidence",
            },
            {
                "Ticker": "SHORTCO",
                "Current Price": short_price,
                "Recommended Action": "SHORT",
                "Combined Long Score": 0.0,
                "Combined Short Score": 1.0,
                "Reason": "short evidence",
            },
        ]

    def test_gross_exposure_never_exceeds_starting_capital(self):
        portfolio = build_paper_portfolio(
            self._rows(),
            starting_capital=100_000.0,
            allow_shorts=True,
            max_long_position_value=100_000.0,
            max_short_position_value=100_000.0,
        )
        self.assertLessEqual(portfolio["Gross Exposure"], 100_000.0)
        self.assertEqual(
            portfolio["Gross Exposure"],
            portfolio["Gross Long Exposure"] + portfolio["Gross Short Exposure"],
        )

    def test_40k_short_leaves_only_60k_capacity_when_no_longs_taken(self):
        """Example 1: Start=$100,000, Short=$40,000, Long=$0 -> capacity=$60,000."""
        rows = [
            {
                "Ticker": "SHORTCO",
                "Current Price": 50.0,
                "Recommended Action": "SHORT",
                "Combined Long Score": 0.0,
                "Combined Short Score": 1.0,
                "Reason": "short evidence",
            }
        ]
        portfolio = build_paper_portfolio(
            rows,
            starting_capital=100_000.0,
            allow_shorts=True,
            max_long_position_value=100_000.0,
            max_short_position_value=40_000.0,
        )
        self.assertEqual(portfolio["Gross Long Exposure"], 0.0)
        self.assertEqual(portfolio["Gross Short Exposure"], 40_000.0)
        self.assertEqual(portfolio["Remaining Capacity"], 60_000.0)

    def test_70k_long_and_30k_short_leaves_zero_capacity(self):
        """Example 2: Long=$70,000, Short=$30,000 -> capacity=$0."""
        rows = [
            {
                "Ticker": "LONGCO",
                "Current Price": 100.0,
                "Recommended Action": "LONG",
                "Combined Long Score": 1.0,
                "Combined Short Score": 0.0,
                "Reason": "long evidence",
            },
            {
                "Ticker": "SHORTCO",
                "Current Price": 100.0,
                "Recommended Action": "SHORT",
                "Combined Long Score": 0.0,
                "Combined Short Score": 1.0,
                "Reason": "short evidence",
            },
        ]
        portfolio = build_paper_portfolio(
            rows,
            starting_capital=100_000.0,
            allow_shorts=True,
            max_long_position_value=70_000.0,
            max_short_position_value=30_000.0,
        )
        self.assertEqual(portfolio["Gross Long Exposure"], 70_000.0)
        self.assertEqual(portfolio["Gross Short Exposure"], 30_000.0)
        self.assertEqual(portfolio["Gross Exposure"], 100_000.0)
        self.assertEqual(portfolio["Remaining Capacity"], 0.0)

    def test_short_exposure_never_increases_long_buying_power(self):
        portfolio = build_paper_portfolio(
            self._rows(),
            starting_capital=20_000.0,
            allow_shorts=True,
            max_long_position_value=20_000.0,
            max_short_position_value=20_000.0,
        )
        # Long allocation used the full $20k shared pool first, leaving
        # nothing for the short - $20k of shorts on top would have made
        # gross exposure $40k on a $20k account, which must never happen.
        self.assertEqual(portfolio["Gross Long Exposure"], 20_000.0)
        self.assertEqual(portfolio["Remaining Capacity"], 0.0)
        self.assertEqual(portfolio["Gross Short Exposure"], 0.0)
        self.assertEqual(portfolio["Net Exposure"], 20_000.0)

    def test_no_candidates_leaves_all_capacity_unused(self):
        rows = [
            {
                "Ticker": "FLAT",
                "Current Price": 100.0,
                "Recommended Action": "WATCH",
                "Combined Long Score": 0.0,
                "Combined Short Score": 0.0,
                "Reason": "no evidence",
            }
        ]
        portfolio = build_paper_portfolio(
            rows, starting_capital=100_000.0, allow_shorts=True
        )
        self.assertEqual(portfolio["Longs"], [])
        self.assertEqual(portfolio["Shorts"], [])
        self.assertEqual(portfolio["Remaining Capacity"], 100_000.0)
        self.assertEqual(portfolio["Gross Long Exposure"], 0.0)
        self.assertEqual(portfolio["Gross Short Exposure"], 0.0)
        self.assertEqual(portfolio["Gross Exposure"], 0.0)

    def test_ineligible_low_priced_stock_is_excluded(self):
        rows = [
            {
                "Ticker": "PENNY",
                "Current Price": 2.0,
                "Recommended Action": "LONG",
                "Combined Long Score": 5.0,
                "Combined Short Score": 0.0,
                "Reason": "strong but too cheap",
            }
        ]
        portfolio = build_paper_portfolio(
            rows, starting_capital=100_000.0, allow_shorts=True, min_stock_price=5.0
        )
        self.assertEqual(portfolio["Longs"], [])
        self.assertEqual(portfolio["Remaining Capacity"], 100_000.0)


if __name__ == "__main__":
    unittest.main()
