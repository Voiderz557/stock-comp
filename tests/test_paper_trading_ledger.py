import unittest

from paper_trading.ledger import (
    FAIL,
    INSOLVENCY,
    MARKET_BREACH,
    PASS,
    UNAVAILABLE,
    reconcile_ledger,
)


class LedgerReconciliationTests(unittest.TestCase):
    def _account(self, cash=80_000.0, realized=0.0, starting=100_000.0):
        return {
            "Cash": cash,
            "Realized P&L": realized,
            "Starting Capital": starting,
            "Updated At": "2025-01-01T00:00:00+00:00",
        }

    def test_mixed_book_passes_with_prices(self):
        opens = [
            {
                "id": 1,
                "ticker": "AAPL",
                "direction": "LONG",
                "entry_price": 100.0,
                "quantity": 100.0,
                "allocated_capital": 10_000.0,
            },
            {
                "id": 2,
                "ticker": "TSLA",
                "direction": "SHORT",
                "entry_price": 200.0,
                "quantity": 50.0,
                "allocated_capital": 10_000.0,
            },
        ]
        report = reconcile_ledger(
            self._account(),
            opens,
            [],
            {"AAPL": 110.0, "TSLA": 180.0},
        )
        self.assertEqual(report["Accounting"], PASS)
        self.assertEqual(report["Valuation"], PASS)
        self.assertAlmostEqual(report["Equity"], 102_000.0)
        statuses = {check["Name"]: check["Status"] for check in report["Checks"]}
        self.assertNotIn(UNAVAILABLE, statuses.values())

    def test_missing_prices_are_unavailable_not_pass(self):
        opens = [
            {
                "id": 1,
                "ticker": "AAPL",
                "direction": "LONG",
                "entry_price": 100.0,
                "quantity": 100.0,
                "allocated_capital": 10_000.0,
            }
        ]
        report = reconcile_ledger(self._account(cash=90_000.0), opens, [], {})
        self.assertEqual(report["Accounting"], PASS)
        self.assertEqual(report["Valuation"], UNAVAILABLE)
        self.assertEqual(report["Status"], UNAVAILABLE)
        valuation_checks = [
            check for check in report["Checks"] if check["Status"] == UNAVAILABLE
        ]
        self.assertTrue(valuation_checks)
        self.assertFalse(any(check["Status"] == PASS and "Valuation" in check["Name"] for check in report["Checks"]))

    def test_corrupted_allocation_fails(self):
        opens = [
            {
                "id": 1,
                "ticker": "AAPL",
                "direction": "LONG",
                "entry_price": 100.0,
                "quantity": 100.0,
                "allocated_capital": 9_000.0,
            }
        ]
        report = reconcile_ledger(self._account(cash=91_000.0), opens, [], {"AAPL": 100.0})
        self.assertEqual(report["Accounting"], FAIL)

    def test_closed_pnl_mismatch_fails(self):
        closed = [
            {
                "id": 1,
                "ticker": "AAPL",
                "direction": "LONG",
                "entry_price": 100.0,
                "exit_price": 110.0,
                "quantity": 10.0,
                "allocated_capital": 1_000.0,
                "realized_pnl": 50.0,
            }
        ]
        report = reconcile_ledger(
            self._account(cash=100_050.0, realized=50.0), [], closed, {}
        )
        self.assertEqual(report["Accounting"], FAIL)

    def test_market_breach_is_not_accounting_failure(self):
        opens = [
            {
                "id": 1,
                "ticker": "AAPL",
                "direction": "LONG",
                "entry_price": 100.0,
                "quantity": 800.0,
                "allocated_capital": 80_000.0,
            }
        ]
        report = reconcile_ledger(
            self._account(cash=20_000.0), opens, [], {"AAPL": 150.0}
        )
        self.assertEqual(report["Accounting"], PASS)
        self.assertEqual(report["Valuation"], MARKET_BREACH)
        self.assertEqual(report["Status"], MARKET_BREACH)

    def test_large_short_loss_is_insolvency_not_fail(self):
        closed = [
            {
                "id": 1,
                "ticker": "TSLA",
                "direction": "SHORT",
                "entry_price": 100.0,
                "exit_price": 1_000.0,
                "quantity": 200.0,
                "allocated_capital": 20_000.0,
                "realized_pnl": -180_000.0,
            }
        ]
        report = reconcile_ledger(
            self._account(cash=-80_000.0, realized=-180_000.0), [], closed, {}
        )
        self.assertEqual(report["Accounting"], PASS)
        self.assertEqual(report["Solvency"], INSOLVENCY)


if __name__ == "__main__":
    unittest.main()
