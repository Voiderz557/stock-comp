import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from paper_trading import storage


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
            db_path=self.db_path, ticker="BBB", entry_price=10.0, quantity=10
        )
        storage.close_position(
            first_id, exit_price=11.0, exit_timestamp="2025-01-01T00:00:00+00:00", db_path=self.db_path
        )
        storage.close_position(
            second_id, exit_price=12.0, exit_timestamp="2025-02-01T00:00:00+00:00", db_path=self.db_path
        )
        closed = storage.get_closed_trades(self.db_path)
        self.assertEqual([trade["ticker"] for trade in closed], ["BBB", "AAA"])


if __name__ == "__main__":
    unittest.main()
