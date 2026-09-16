import sqlite3
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from paper_trading import storage
from paper_trading.portfolio import compute_exposure_summary


class StorageTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = TemporaryDirectory()
        self.db_path = Path(self._tmp_dir.name) / "paper_portfolio_test.sqlite3"

    def tearDown(self):
        self._tmp_dir.cleanup()

    def _init(self, starting_capital=100_000.0):
        storage.initialize_storage(self.db_path, starting_capital=starting_capital)


class InitializationTests(StorageTestCase):
    def test_initialize_seeds_account_state(self):
        self._init(starting_capital=50_000.0)
        account = storage.get_account_state(self.db_path)
        self.assertEqual(account["Cash"], 50_000.0)
        self.assertEqual(account["Realized P&L"], 0.0)
        self.assertEqual(account["Starting Capital"], 50_000.0)

    def test_reinitialize_does_not_backfill_null_starting_capital_from_cash(self):
        self._init(starting_capital=100_000.0)
        storage.open_position(
            db_path=self.db_path, ticker="AAPL", entry_price=100.0, allocated_capital=10_000.0
        )
        connection = sqlite3.connect(self.db_path)
        connection.execute("UPDATE account_state SET starting_capital = NULL WHERE id = 1")
        connection.commit()
        connection.close()

        storage.initialize_storage(self.db_path, starting_capital=999_999.0)
        account = storage.get_account_state(self.db_path)
        self.assertIsNone(account["Starting Capital"])
        self.assertAlmostEqual(account["Cash"], 90_000.0)

    def test_initialize_is_idempotent(self):
        self._init(starting_capital=50_000.0)
        storage.open_position(
            db_path=self.db_path, ticker="AAPL", entry_price=100.0, allocated_capital=10_000.0
        )
        # Calling initialize_storage again must not reset cash/positions.
        storage.initialize_storage(self.db_path, starting_capital=999_999.0)
        account = storage.get_account_state(self.db_path)
        self.assertEqual(account["Cash"], 40_000.0)
        self.assertEqual(len(storage.get_open_positions(self.db_path)), 1)

    def test_get_account_state_without_init_raises(self):
        with self.assertRaises(RuntimeError):
            storage.get_account_state(self.db_path)


class OpenPositionTests(StorageTestCase):
    def setUp(self):
        super().setUp()
        self._init(starting_capital=100_000.0)

    def test_open_position_with_allocated_capital_derives_quantity(self):
        position_id = storage.open_position(
            db_path=self.db_path,
            ticker="AAPL",
            entry_price=200.0,
            allocated_capital=10_000.0,
            strategy="Baseline",
            reason="test buy",
        )
        positions = storage.get_open_positions(self.db_path)
        self.assertEqual(len(positions), 1)
        position = positions[0]
        self.assertEqual(position["id"], position_id)
        self.assertEqual(position["ticker"], "AAPL")
        self.assertEqual(position["direction"], "LONG")
        self.assertAlmostEqual(position["quantity"], 50.0)
        self.assertAlmostEqual(position["allocated_capital"], 10_000.0)

    def test_open_position_with_quantity_derives_allocated_capital(self):
        storage.open_position(
            db_path=self.db_path, ticker="MSFT", entry_price=50.0, quantity=20.0
        )
        position = storage.get_open_positions(self.db_path)[0]
        self.assertAlmostEqual(position["allocated_capital"], 1_000.0)

    def test_open_position_debits_cash(self):
        storage.open_position(
            db_path=self.db_path, ticker="AAPL", entry_price=200.0, allocated_capital=10_000.0
        )
        account = storage.get_account_state(self.db_path)
        self.assertAlmostEqual(account["Cash"], 90_000.0)

    def test_open_position_rejects_insufficient_cash(self):
        with self.assertRaises(ValueError):
            storage.open_position(
                db_path=self.db_path,
                ticker="AAPL",
                entry_price=200.0,
                allocated_capital=1_000_000.0,
            )
        # No partial state change on rejection.
        self.assertEqual(storage.get_open_positions(self.db_path), [])
        self.assertAlmostEqual(storage.get_account_state(self.db_path)["Cash"], 100_000.0)

    def test_open_position_rejects_non_positive_values(self):
        with self.assertRaises(ValueError):
            storage.open_position(db_path=self.db_path, ticker="AAPL", entry_price=0)
        with self.assertRaises(ValueError):
            storage.open_position(
                db_path=self.db_path, ticker="AAPL", entry_price=100.0, allocated_capital=-5
            )
        with self.assertRaises(ValueError):
            storage.open_position(
                db_path=self.db_path, ticker="AAPL", entry_price=float("nan"), allocated_capital=10.0
            )
        with self.assertRaises(ValueError):
            storage.open_position(
                db_path=self.db_path, ticker="AAPL", entry_price=100.0, quantity=float("inf")
            )

    def test_open_position_rejects_inconsistent_quantity_and_allocation(self):
        with self.assertRaises(ValueError):
            storage.open_position(
                db_path=self.db_path,
                ticker="AAPL",
                entry_price=100.0,
                quantity=10.0,
                allocated_capital=500.0,
            )
        self.assertEqual(storage.get_open_positions(self.db_path), [])
        self.assertAlmostEqual(storage.get_account_state(self.db_path)["Cash"], 100_000.0)

    def test_open_position_accepts_consistent_quantity_and_allocation(self):
        storage.open_position(
            db_path=self.db_path,
            ticker="AAPL",
            entry_price=100.0,
            quantity=10.0,
            allocated_capital=1_000.0,
        )
        position = storage.get_open_positions(self.db_path)[0]
        self.assertAlmostEqual(position["quantity"], 10.0)
        self.assertAlmostEqual(position["allocated_capital"], 1_000.0)

    def test_open_position_requires_quantity_or_allocation(self):
        with self.assertRaises(ValueError):
            storage.open_position(db_path=self.db_path, ticker="AAPL", entry_price=100.0)


class ClosePositionTests(StorageTestCase):
    def setUp(self):
        super().setUp()
        self._init(starting_capital=100_000.0)
        self.position_id = storage.open_position(
            db_path=self.db_path,
            ticker="AAPL",
            entry_price=100.0,
            allocated_capital=10_000.0,
            strategy="Baseline",
            reason="test buy",
        )

    def test_close_long_position_profit(self):
        realized = storage.close_position(self.position_id, exit_price=110.0, db_path=self.db_path)
        # 100 shares * $10 gain = $1,000.
        self.assertAlmostEqual(realized, 1_000.0)

        self.assertEqual(storage.get_open_positions(self.db_path), [])
        closed = storage.get_closed_trades(self.db_path)
        self.assertEqual(len(closed), 1)
        self.assertAlmostEqual(closed[0]["realized_pnl"], 1_000.0)
        self.assertEqual(closed[0]["strategy"], "Baseline")

        account = storage.get_account_state(self.db_path)
        # Started with 90,000 cash after opening; closing returns 10,000 + 1,000 profit.
        self.assertAlmostEqual(account["Cash"], 101_000.0)
        self.assertAlmostEqual(account["Realized P&L"], 1_000.0)

    def test_close_long_position_loss(self):
        realized = storage.close_position(self.position_id, exit_price=90.0, db_path=self.db_path)
        self.assertAlmostEqual(realized, -1_000.0)
        account = storage.get_account_state(self.db_path)
        self.assertAlmostEqual(account["Cash"], 99_000.0)
        self.assertAlmostEqual(account["Realized P&L"], -1_000.0)

    def test_close_short_returns_allocated_capital_plus_realized_pnl(self):
        short_id = storage.open_position(
            db_path=self.db_path,
            ticker="TSLA",
            direction="SHORT",
            entry_price=200.0,
            allocated_capital=20_000.0,
            current_prices={"AAPL": 100.0},
        )
        # After the original $10k long and this $20k short, cash is $70k.
        self.assertAlmostEqual(storage.get_account_state(self.db_path)["Cash"], 70_000.0)

        # Price falls: short profit = 100 shares * $20 = $2,000.
        realized = storage.close_position(short_id, exit_price=180.0, db_path=self.db_path)
        self.assertAlmostEqual(realized, 2_000.0)
        account = storage.get_account_state(self.db_path)
        self.assertAlmostEqual(account["Cash"], 92_000.0)
        self.assertAlmostEqual(account["Realized P&L"], 2_000.0)

    def test_close_unknown_position_raises(self):
        with self.assertRaises(ValueError):
            storage.close_position(999_999, exit_price=100.0, db_path=self.db_path)

    def test_close_position_rejects_non_positive_price(self):
        with self.assertRaises(ValueError):
            storage.close_position(self.position_id, exit_price=0, db_path=self.db_path)


class QueryOrderingTests(StorageTestCase):
    def setUp(self):
        super().setUp()
        self._init(starting_capital=100_000.0)

    def test_closed_trades_are_most_recent_first(self):
        first_id = storage.open_position(
            db_path=self.db_path, ticker="AAA", entry_price=10.0, quantity=10
        )
        second_id = storage.open_position(
            db_path=self.db_path,
            ticker="BBB",
            entry_price=10.0,
            quantity=10,
            current_prices={"AAA": 10.0},
        )
        storage.close_position(
            first_id, exit_price=11.0, exit_timestamp="2025-01-01T00:00:00+00:00", db_path=self.db_path
        )
        storage.close_position(
            second_id, exit_price=12.0, exit_timestamp="2025-02-01T00:00:00+00:00", db_path=self.db_path
        )
        closed = storage.get_closed_trades(self.db_path)
        self.assertEqual([trade["ticker"] for trade in closed], ["BBB", "AAA"])


class SharedCapacityTests(StorageTestCase):
    def setUp(self):
        super().setUp()
        self._init(starting_capital=100_000.0)

    def test_40k_short_debits_cash_and_leaves_60k_capacity(self):
        storage.open_position(
            db_path=self.db_path,
            ticker="TSLA",
            direction="SHORT",
            entry_price=200.0,
            allocated_capital=40_000.0,
        )
        account = storage.get_account_state(self.db_path)
        self.assertAlmostEqual(account["Cash"], 60_000.0)
        exposure = compute_exposure_summary(
            storage.get_open_positions(self.db_path),
            {"TSLA": 200.0},
            account["Starting Capital"],
        )
        self.assertAlmostEqual(exposure["Current Short Exposure"], 40_000.0)
        self.assertAlmostEqual(exposure["Gross Exposure"], 40_000.0)
        self.assertAlmostEqual(exposure["Remaining Capacity"], 60_000.0)

        storage.open_position(
            db_path=self.db_path,
            ticker="AAPL",
            direction="LONG",
            entry_price=100.0,
            allocated_capital=60_000.0,
            current_prices={"TSLA": 200.0},
        )
        self.assertAlmostEqual(storage.get_account_state(self.db_path)["Cash"], 0.0)

    def test_70k_long_and_30k_short_leaves_zero_capacity(self):
        storage.open_position(
            db_path=self.db_path,
            ticker="AAPL",
            direction="LONG",
            entry_price=100.0,
            allocated_capital=70_000.0,
        )
        storage.open_position(
            db_path=self.db_path,
            ticker="TSLA",
            direction="SHORT",
            entry_price=100.0,
            allocated_capital=30_000.0,
            current_prices={"AAPL": 100.0},
        )
        exposure = compute_exposure_summary(
            storage.get_open_positions(self.db_path),
            {"AAPL": 100.0, "TSLA": 100.0},
            100_000.0,
        )
        self.assertAlmostEqual(exposure["Current Long Exposure"], 70_000.0)
        self.assertAlmostEqual(exposure["Current Short Exposure"], 30_000.0)
        self.assertAlmostEqual(exposure["Gross Exposure"], 100_000.0)
        self.assertAlmostEqual(exposure["Remaining Capacity"], 0.0)
        self.assertAlmostEqual(storage.get_account_state(self.db_path)["Cash"], 0.0)

    def test_mixed_positions_cannot_exceed_starting_capital(self):
        storage.open_position(
            db_path=self.db_path,
            ticker="AAPL",
            direction="LONG",
            entry_price=100.0,
            allocated_capital=60_000.0,
        )
        with self.assertRaises(ValueError):
            storage.open_position(
                db_path=self.db_path,
                ticker="TSLA",
                direction="SHORT",
                entry_price=100.0,
                allocated_capital=50_000.0,
                current_prices={"AAPL": 100.0},
            )
        self.assertEqual(len(storage.get_open_positions(self.db_path)), 1)
        self.assertAlmostEqual(storage.get_account_state(self.db_path)["Cash"], 40_000.0)

    def test_cash_allows_but_capacity_rejects_after_price_rise(self):
        storage.open_position(
            db_path=self.db_path,
            ticker="AAPL",
            direction="LONG",
            entry_price=100.0,
            allocated_capital=40_000.0,
        )
        # 400 shares now worth $200: long exposure $80k, remaining capacity $20k,
        # cash still $60k.
        with self.assertRaises(ValueError):
            storage.open_position(
                db_path=self.db_path,
                ticker="MSFT",
                direction="LONG",
                entry_price=100.0,
                allocated_capital=30_000.0,
                current_prices={"AAPL": 200.0},
            )
        self.assertEqual(len(storage.get_open_positions(self.db_path)), 1)
        self.assertAlmostEqual(storage.get_account_state(self.db_path)["Cash"], 60_000.0)

        storage.open_position(
            db_path=self.db_path,
            ticker="MSFT",
            direction="LONG",
            entry_price=100.0,
            allocated_capital=20_000.0,
            current_prices={"AAPL": 200.0},
        )
        self.assertEqual(len(storage.get_open_positions(self.db_path)), 2)

    def test_capacity_allows_but_cash_rejects_after_price_drop(self):
        storage.open_position(
            db_path=self.db_path,
            ticker="AAPL",
            direction="LONG",
            entry_price=100.0,
            allocated_capital=90_000.0,
        )
        # 900 shares now worth $50: long exposure $45k, remaining capacity $55k,
        # cash only $10k.
        with self.assertRaises(ValueError):
            storage.open_position(
                db_path=self.db_path,
                ticker="MSFT",
                direction="LONG",
                entry_price=100.0,
                allocated_capital=40_000.0,
                current_prices={"AAPL": 50.0},
            )
        self.assertEqual(len(storage.get_open_positions(self.db_path)), 1)
        self.assertAlmostEqual(storage.get_account_state(self.db_path)["Cash"], 10_000.0)

    def test_price_driven_breach_blocks_opens_but_not_closes(self):
        position_id = storage.open_position(
            db_path=self.db_path,
            ticker="AAPL",
            direction="LONG",
            entry_price=100.0,
            allocated_capital=80_000.0,
        )
        with self.assertRaises(ValueError):
            storage.open_position(
                db_path=self.db_path,
                ticker="MSFT",
                direction="LONG",
                entry_price=100.0,
                allocated_capital=1_000.0,
                current_prices={"AAPL": 150.0},
            )
        realized = storage.close_position(position_id, exit_price=150.0, db_path=self.db_path)
        self.assertAlmostEqual(realized, 40_000.0)
        self.assertEqual(storage.get_open_positions(self.db_path), [])
        self.assertAlmostEqual(storage.get_account_state(self.db_path)["Cash"], 140_000.0)

    def test_missing_current_price_blocks_new_open_without_mutation(self):
        storage.open_position(
            db_path=self.db_path,
            ticker="AAPL",
            direction="LONG",
            entry_price=100.0,
            allocated_capital=10_000.0,
        )
        with self.assertRaises(ValueError) as context:
            storage.open_position(
                db_path=self.db_path,
                ticker="MSFT",
                direction="LONG",
                entry_price=100.0,
                allocated_capital=10_000.0,
            )
        self.assertIn("missing current prices", str(context.exception).lower())
        self.assertIn("will not fall back", str(context.exception).lower())
        self.assertEqual(len(storage.get_open_positions(self.db_path)), 1)
        self.assertAlmostEqual(storage.get_account_state(self.db_path)["Cash"], 90_000.0)

    def test_invalid_current_price_blocks_new_open_without_mutation(self):
        storage.open_position(
            db_path=self.db_path,
            ticker="AAPL",
            direction="LONG",
            entry_price=100.0,
            allocated_capital=10_000.0,
        )
        for bad_price in (float("nan"), float("inf"), 0.0, -5.0):
            with self.assertRaises(ValueError):
                storage.open_position(
                    db_path=self.db_path,
                    ticker="MSFT",
                    direction="LONG",
                    entry_price=100.0,
                    allocated_capital=1_000.0,
                    current_prices={"AAPL": bad_price},
                )
        self.assertEqual(len(storage.get_open_positions(self.db_path)), 1)
        self.assertAlmostEqual(storage.get_account_state(self.db_path)["Cash"], 90_000.0)

    def test_unknown_starting_capital_blocks_open_without_mutation(self):
        connection = sqlite3.connect(self.db_path)
        connection.execute("UPDATE account_state SET starting_capital = NULL WHERE id = 1")
        connection.commit()
        connection.close()
        with self.assertRaises(ValueError):
            storage.open_position(
                db_path=self.db_path,
                ticker="AAPL",
                direction="LONG",
                entry_price=100.0,
                allocated_capital=1_000.0,
            )
        self.assertEqual(storage.get_open_positions(self.db_path), [])
        self.assertAlmostEqual(storage.get_account_state(self.db_path)["Cash"], 100_000.0)

    def test_restart_preserves_capacity_calculation(self):
        storage.open_position(
            db_path=self.db_path,
            ticker="AAPL",
            direction="LONG",
            entry_price=100.0,
            allocated_capital=60_000.0,
        )
        storage.open_position(
            db_path=self.db_path,
            ticker="TSLA",
            direction="SHORT",
            entry_price=200.0,
            allocated_capital=30_000.0,
            current_prices={"AAPL": 100.0},
        )
        current_prices = {"AAPL": 110.0, "TSLA": 180.0}
        before = compute_exposure_summary(
            storage.get_open_positions(self.db_path),
            current_prices,
            storage.get_account_state(self.db_path)["Starting Capital"],
        )
        # A new process is a new connection to the same file.
        after_account = storage.get_account_state(self.db_path)
        after = compute_exposure_summary(
            storage.get_open_positions(self.db_path),
            current_prices,
            after_account["Starting Capital"],
        )
        self.assertEqual(before, after)
        self.assertAlmostEqual(after["Current Long Exposure"], 66_000.0)
        self.assertAlmostEqual(after["Current Short Exposure"], 27_000.0)
        self.assertAlmostEqual(after["Gross Exposure"], 93_000.0)
        self.assertAlmostEqual(after["Remaining Capacity"], 7_000.0)
        self.assertAlmostEqual(after_account["Cash"], 10_000.0)

    def test_concurrent_opens_cannot_overspend_capacity(self):
        errors = []
        successes = []

        def try_open(ticker):
            try:
                storage.open_position(
                    db_path=self.db_path,
                    ticker=ticker,
                    direction="LONG",
                    entry_price=100.0,
                    allocated_capital=70_000.0,
                    current_prices={},
                )
                successes.append(ticker)
            except Exception as error:
                errors.append(error)

        threads = [
            threading.Thread(target=try_open, args=("AAA",)),
            threading.Thread(target=try_open, args=("BBB",)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        positions = storage.get_open_positions(self.db_path)
        allocated = sum(position["allocated_capital"] for position in positions)
        self.assertLessEqual(allocated, 100_000.0 + 1e-6)
        self.assertEqual(len(successes), 1)
        self.assertEqual(len(errors), 1)
        self.assertAlmostEqual(
            storage.get_account_state(self.db_path)["Cash"], 30_000.0
        )


if __name__ == "__main__":
    unittest.main()
