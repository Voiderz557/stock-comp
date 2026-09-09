import tempfile
import unittest
from pathlib import Path

from paper_trading.storage import LONG, SHORT, PaperPortfolioStore


class PersistenceTests(unittest.TestCase):
    """Every position/cash/trade must survive closing and reopening the
    store, simulating a full app restart (not just calling more methods on
    the same in-memory object)."""

    def test_open_position_survives_restart(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "portfolio.sqlite3"

            store = PaperPortfolioStore(db_path, starting_cash=100_000.0)
            opened = store.open_position(
                ticker="AAPL",
                direction=LONG,
                entry_price=200.0,
                allocated_capital=20_000.0,
                strategy="Momentum V2",
                reason="test open",
            )
            store.close()  # simulate the app process shutting down

            # A brand-new store instance pointed at the same file simulates
            # reopening the app.
            reopened_store = PaperPortfolioStore(db_path, starting_cash=100_000.0)
            positions = reopened_store.list_open_positions()
            reopened_store.close()

        self.assertEqual(len(positions), 1)
        restored = positions[0]
        self.assertEqual(restored.id, opened.id)
        self.assertEqual(restored.ticker, "AAPL")
        self.assertEqual(restored.direction, LONG)
        self.assertEqual(restored.entry_price, 200.0)
        self.assertEqual(restored.quantity, 100.0)
        self.assertEqual(restored.allocated_capital, 20_000.0)
        self.assertEqual(restored.strategy, "Momentum V2")

    def test_cash_survives_restart(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "portfolio.sqlite3"

            store = PaperPortfolioStore(db_path, starting_cash=100_000.0)
            store.open_position(
                ticker="MSFT",
                direction=LONG,
                entry_price=400.0,
                allocated_capital=20_000.0,
                strategy="Baseline",
            )
            cash_before_restart = store.get_cash()
            store.close()

            reopened_store = PaperPortfolioStore(db_path, starting_cash=100_000.0)
            cash_after_restart = reopened_store.get_cash()
            reopened_store.close()

        self.assertEqual(cash_before_restart, 80_000.0)
        self.assertEqual(cash_after_restart, 80_000.0)

    def test_reopening_never_reseeds_a_second_starting_balance(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "portfolio.sqlite3"

            store = PaperPortfolioStore(db_path, starting_cash=100_000.0)
            store.open_position(
                ticker="MSFT",
                direction=LONG,
                entry_price=400.0,
                allocated_capital=20_000.0,
                strategy="Baseline",
            )
            store.close()

            # Even if a different starting_cash is passed on "restart" (e.g.
            # the config default changed), the persisted cash must win - the
            # database is not silently reseeded/reset.
            reopened_store = PaperPortfolioStore(db_path, starting_cash=999_999.0)
            cash = reopened_store.get_cash()
            reopened_store.close()

        self.assertEqual(cash, 80_000.0)

    def test_closed_trades_remain_in_history_after_restart(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "portfolio.sqlite3"

            store = PaperPortfolioStore(db_path, starting_cash=100_000.0)
            position = store.open_position(
                ticker="NVDA",
                direction=LONG,
                entry_price=100.0,
                allocated_capital=10_000.0,
                strategy="Aggressive Momentum V1",
                reason="strong breakout",
            )
            realized_pnl = store.close_position(position.id, exit_price=120.0)
            store.close()

            reopened_store = PaperPortfolioStore(db_path, starting_cash=100_000.0)
            open_positions = reopened_store.list_open_positions()
            closed_trades = reopened_store.list_closed_trades()
            cash = reopened_store.get_cash()
            total_realized_pnl = reopened_store.get_realized_pnl()
            reopened_store.close()

        self.assertEqual(open_positions, [])
        self.assertEqual(len(closed_trades), 1)
        trade = closed_trades[0]
        self.assertEqual(trade.ticker, "NVDA")
        self.assertEqual(trade.entry_price, 100.0)
        self.assertEqual(trade.exit_price, 120.0)
        self.assertAlmostEqual(realized_pnl, 2_000.0)
        self.assertAlmostEqual(trade.realized_pnl, 2_000.0)
        # LONG close credits full sale proceeds (quantity * exit_price) to
        # cash: 100_000 - 10_000 (opened) + 100 shares * $120 = 102_000.
        self.assertAlmostEqual(cash, 102_000.0)
        self.assertAlmostEqual(total_realized_pnl, 2_000.0)

    def test_short_open_does_not_change_cash_and_close_settles_pnl_only(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "portfolio.sqlite3"

            store = PaperPortfolioStore(db_path, starting_cash=100_000.0)
            cash_before_open = store.get_cash()
            position = store.open_position(
                ticker="TSLA",
                direction=SHORT,
                entry_price=300.0,
                allocated_capital=15_000.0,
                strategy="Aggressive Momentum V1",
            )
            cash_after_open = store.get_cash()

            # Price falls -> short position profits.
            realized_pnl = store.close_position(position.id, exit_price=270.0)
            cash_after_close = store.get_cash()
            store.close()

        # Opening a SHORT must not change cash - proceeds are never assumed
        # spendable, and there is no margin/leverage modeled.
        self.assertEqual(cash_before_open, cash_after_open)
        # 50 shares short * ($300 - $270) = $1,500 profit, settled to cash
        # only on close.
        self.assertAlmostEqual(realized_pnl, 1_500.0)
        self.assertAlmostEqual(cash_after_close, cash_before_open + 1_500.0)

    def test_valuation_recalculates_without_overwriting_entry_price(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "portfolio.sqlite3"

            store = PaperPortfolioStore(db_path, starting_cash=100_000.0)
            position = store.open_position(
                ticker="GOOGL",
                direction=LONG,
                entry_price=150.0,
                allocated_capital=15_000.0,
                strategy="Momentum V2",
            )

            valuation = store.valuation_summary({"GOOGL": 180.0})
            # Recalculating with a current price must not mutate storage.
            reloaded_position = store.get_open_position(position.id)
            store.close()

        self.assertEqual(reloaded_position.entry_price, 150.0)
        self.assertEqual(reloaded_position.quantity, 100.0)

        priced_row = valuation["Positions"][0]
        self.assertEqual(priced_row["Current Price"], 180.0)
        self.assertAlmostEqual(priced_row["Market Value"], 18_000.0)
        # (180 - 150) * 100 shares = 3,000 unrealized gain.
        self.assertAlmostEqual(priced_row["Unrealized P&L"], 3_000.0)
        self.assertAlmostEqual(valuation["Total Unrealized P&L"], 3_000.0)
        # Cash (85,000 after the buy) + market value (18,000).
        self.assertAlmostEqual(valuation["Total Portfolio Value"], 103_000.0)

    def test_short_exposure_uses_current_price_not_entry_price(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "portfolio.sqlite3"
            store = PaperPortfolioStore(db_path, starting_cash=100_000.0)
            store.open_position(
                ticker="TSLA",
                direction=SHORT,
                entry_price=100.0,
                allocated_capital=10_000.0,  # 100 shares short at $100
                strategy="Aggressive Momentum V1",
            )

            valuation = store.valuation_summary({"TSLA": 100.0})
            store.close()

        row = valuation["Positions"][0]
        # At the moment of opening, current price == entry price, so both
        # exposures start out equal.
        self.assertAlmostEqual(row["Initial Short Exposure"], 10_000.0)
        self.assertAlmostEqual(row["Current Short Exposure"], 10_000.0)

    def test_short_exposure_rises_when_price_rises(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "portfolio.sqlite3"
            store = PaperPortfolioStore(db_path, starting_cash=100_000.0)
            position = store.open_position(
                ticker="TSLA",
                direction=SHORT,
                entry_price=100.0,
                allocated_capital=10_000.0,  # 100 shares short at $100
                strategy="Aggressive Momentum V1",
            )

            valuation = store.valuation_summary({"TSLA": 150.0})
            reloaded_position = store.get_open_position(position.id)
            store.close()

        row = valuation["Positions"][0]
        # Entry price permanently stored and unaffected by the price move.
        self.assertEqual(reloaded_position.entry_price, 100.0)
        # Initial exposure never changes...
        self.assertAlmostEqual(row["Initial Short Exposure"], 10_000.0)
        # ...but current exposure RISES with the price: 100 shares * $150.
        self.assertAlmostEqual(row["Current Short Exposure"], 15_000.0)
        self.assertGreater(
            row["Current Short Exposure"], row["Initial Short Exposure"]
        )
        # Unrealized P&L still moves the OPPOSITE way for a short: a $50
        # price rise against a 100-share short is a $5,000 loss.
        self.assertAlmostEqual(row["Unrealized P&L"], -5_000.0)
        self.assertAlmostEqual(valuation["Current Short Exposure"], 15_000.0)

    def test_short_exposure_falls_when_price_falls(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "portfolio.sqlite3"
            store = PaperPortfolioStore(db_path, starting_cash=100_000.0)
            position = store.open_position(
                ticker="TSLA",
                direction=SHORT,
                entry_price=100.0,
                allocated_capital=10_000.0,  # 100 shares short at $100
                strategy="Aggressive Momentum V1",
            )

            valuation = store.valuation_summary({"TSLA": 60.0})
            reloaded_position = store.get_open_position(position.id)
            store.close()

        row = valuation["Positions"][0]
        self.assertEqual(reloaded_position.entry_price, 100.0)
        self.assertAlmostEqual(row["Initial Short Exposure"], 10_000.0)
        # Current exposure FALLS with the price: 100 shares * $60.
        self.assertAlmostEqual(row["Current Short Exposure"], 6_000.0)
        self.assertLess(row["Current Short Exposure"], row["Initial Short Exposure"])
        # A $40 price drop against a 100-share short is a $4,000 gain.
        self.assertAlmostEqual(row["Unrealized P&L"], 4_000.0)
        self.assertAlmostEqual(valuation["Current Short Exposure"], 6_000.0)

    def test_gross_and_net_exposure_combine_current_long_and_short_exposure(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "portfolio.sqlite3"
            store = PaperPortfolioStore(db_path, starting_cash=100_000.0)
            store.open_position(
                ticker="AAPL",
                direction=LONG,
                entry_price=100.0,
                allocated_capital=20_000.0,  # 200 shares long
                strategy="Baseline",
            )
            store.open_position(
                ticker="TSLA",
                direction=SHORT,
                entry_price=100.0,
                allocated_capital=10_000.0,  # 100 shares short
                strategy="Aggressive Momentum V1",
            )

            # Long rises to $110 (current long exposure = 22,000), short
            # rises to $130 (current short exposure = 13,000).
            valuation = store.valuation_summary({"AAPL": 110.0, "TSLA": 130.0})
            store.close()

        self.assertAlmostEqual(valuation["Current Long Exposure"], 22_000.0)
        self.assertAlmostEqual(valuation["Current Short Exposure"], 13_000.0)
        self.assertAlmostEqual(valuation["Gross Exposure"], 35_000.0)
        self.assertAlmostEqual(valuation["Net Exposure"], 9_000.0)

    def test_valuation_handles_missing_current_price_gracefully(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "portfolio.sqlite3"
            store = PaperPortfolioStore(db_path, starting_cash=100_000.0)
            store.open_position(
                ticker="ZZZZ",
                direction=LONG,
                entry_price=50.0,
                allocated_capital=5_000.0,
                strategy="Baseline",
            )
            valuation = store.valuation_summary({})
            store.close()

        priced_row = valuation["Positions"][0]
        self.assertIsNone(priced_row["Current Price"])
        self.assertIsNone(priced_row["Unrealized P&L"])
        # Missing price -> only cash counts toward total value for that
        # position, it is not silently treated as zero P&L.
        self.assertAlmostEqual(valuation["Total Portfolio Value"], 95_000.0)


class ShortSaleAccountingTests(unittest.TestCase):
    """Locks in the rule that starting cash is the ONLY source of long
    buying power - short-sale proceeds/exposure must never inflate it."""

    def test_opening_shorts_does_not_increase_long_buying_power(self):
        """LONG and SHORT share ONE capacity pool: after $40,000 of shorts,
        only $60,000 of LONG capacity remains out of $100,000 total - NOT
        $100,000 (that would make shorts a separate extra wallet)."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "portfolio.sqlite3"
            store = PaperPortfolioStore(db_path, starting_cash=100_000.0)

            self.assertEqual(store.get_cash(), 100_000.0)
            store.open_position(
                ticker="TSLA",
                direction=SHORT,
                entry_price=200.0,
                allocated_capital=40_000.0,
                strategy="Aggressive Momentum V1",
            )
            # Cash/spendable buying power is untouched by the short...
            self.assertEqual(store.get_cash(), 100_000.0)
            # ...but shared gross-exposure capacity has shrunk to $60,000.
            self.assertEqual(
                store.get_remaining_capacity({"TSLA": 200.0}), 60_000.0
            )

            # A $100,000 LONG must now be REJECTED - $40k short + $100k long
            # would be $140,000 of gross exposure on a $100,000 account.
            with self.assertRaises(ValueError):
                store.open_position(
                    ticker="AAPL",
                    direction=LONG,
                    entry_price=100.0,
                    allocated_capital=100_000.0,
                    strategy="Baseline",
                    current_prices={"TSLA": 200.0},
                )
            self.assertEqual(store.get_cash(), 100_000.0)

            # Exactly $60,000 LONG (the remaining shared capacity) is fine.
            store.open_position(
                ticker="AAPL",
                direction=LONG,
                entry_price=100.0,
                allocated_capital=60_000.0,
                strategy="Baseline",
                current_prices={"TSLA": 200.0},
            )
            self.assertEqual(store.get_cash(), 40_000.0)
            self.assertEqual(
                store.get_remaining_capacity({"TSLA": 200.0, "AAPL": 100.0}), 0.0
            )
            store.close()

    def test_cannot_allocate_140k_long_after_opening_40k_of_shorts(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "portfolio.sqlite3"
            store = PaperPortfolioStore(db_path, starting_cash=100_000.0)

            store.open_position(
                ticker="TSLA",
                direction=SHORT,
                entry_price=200.0,
                allocated_capital=40_000.0,
                strategy="Aggressive Momentum V1",
            )

            with self.assertRaises(ValueError):
                store.open_position(
                    ticker="AAPL",
                    direction=LONG,
                    entry_price=100.0,
                    allocated_capital=140_000.0,
                    strategy="Baseline",
                )
            # The failed attempt must not have partially deducted cash.
            self.assertEqual(store.get_cash(), 100_000.0)
            self.assertEqual(len(store.list_open_positions()), 1)
            store.close()

    def test_short_pnl_still_updates_correctly(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "portfolio.sqlite3"
            store = PaperPortfolioStore(db_path, starting_cash=100_000.0)

            position = store.open_position(
                ticker="TSLA",
                direction=SHORT,
                entry_price=200.0,
                allocated_capital=40_000.0,  # 200 shares short
                strategy="Aggressive Momentum V1",
            )

            # Price drops -> short is profitable.
            valuation = store.valuation_summary({"TSLA": 180.0})
            row = valuation["Positions"][0]
            self.assertAlmostEqual(row["Unrealized P&L"], 4_000.0)  # 200 * $20

            # Closing settles that P&L (and only that P&L) to cash.
            realized_pnl = store.close_position(position.id, exit_price=180.0)
            self.assertAlmostEqual(realized_pnl, 4_000.0)
            self.assertAlmostEqual(store.get_cash(), 104_000.0)
            self.assertAlmostEqual(store.get_realized_pnl(), 4_000.0)
            store.close()

    def test_short_exposure_still_updates_with_current_market_price(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "portfolio.sqlite3"
            store = PaperPortfolioStore(db_path, starting_cash=100_000.0)
            store.open_position(
                ticker="TSLA",
                direction=SHORT,
                entry_price=200.0,
                allocated_capital=40_000.0,  # 200 shares short
                strategy="Aggressive Momentum V1",
            )

            valuation_up = store.valuation_summary({"TSLA": 220.0})
            valuation_down = store.valuation_summary({"TSLA": 150.0})
            store.close()

        # 200 shares * $220 = $44,000 (rose above the $40,000 initial exposure).
        self.assertAlmostEqual(valuation_up["Current Short Exposure"], 44_000.0)
        self.assertAlmostEqual(valuation_up["Gross Short Exposure"], 44_000.0)
        # 200 shares * $150 = $30,000 (fell below the $40,000 initial exposure).
        self.assertAlmostEqual(valuation_down["Current Short Exposure"], 30_000.0)
        self.assertAlmostEqual(valuation_down["Gross Short Exposure"], 30_000.0)

    def test_persistence_unaffected_by_short_sale_accounting(self):
        """Opening/valuing a short must not change how state survives a
        full app restart (open positions, cash, closed-trade history)."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "portfolio.sqlite3"

            store = PaperPortfolioStore(db_path, starting_cash=100_000.0)
            store.open_position(
                ticker="TSLA",
                direction=SHORT,
                entry_price=200.0,
                allocated_capital=40_000.0,
                strategy="Aggressive Momentum V1",
            )
            position = store.open_position(
                ticker="AAPL",
                direction=LONG,
                entry_price=100.0,
                allocated_capital=20_000.0,
                strategy="Baseline",
            )
            store.close_position(position.id, exit_price=110.0)
            cash_before_restart = store.get_cash()
            store.close()  # simulate app shutdown

            reopened_store = PaperPortfolioStore(db_path, starting_cash=100_000.0)
            open_positions = reopened_store.list_open_positions()
            closed_trades = reopened_store.list_closed_trades()
            cash_after_restart = reopened_store.get_cash()
            reopened_store.close()

        # Cash: 100,000 - 20,000 (long open) + 22,000 (long close proceeds) = 102,000.
        # The short never touched cash at all.
        self.assertAlmostEqual(cash_before_restart, 102_000.0)
        self.assertAlmostEqual(cash_after_restart, 102_000.0)
        self.assertEqual(len(open_positions), 1)
        self.assertEqual(open_positions[0].ticker, "TSLA")
        self.assertEqual(open_positions[0].direction, SHORT)
        self.assertEqual(open_positions[0].entry_price, 200.0)
        self.assertEqual(len(closed_trades), 1)
        self.assertEqual(closed_trades[0].ticker, "AAPL")


class SharedCapacityAccountingTests(unittest.TestCase):
    """LONG and SHORT must draw from ONE shared gross-exposure pool
    (`starting_capital`), never two separate wallets."""

    def test_gross_exposure_never_exceeds_starting_capital(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "portfolio.sqlite3"
            store = PaperPortfolioStore(db_path, starting_cash=100_000.0)

            store.open_position(
                ticker="AAPL",
                direction=LONG,
                entry_price=100.0,
                allocated_capital=60_000.0,
                strategy="Baseline",
            )
            store.open_position(
                ticker="TSLA",
                direction=SHORT,
                entry_price=100.0,
                allocated_capital=40_000.0,
                strategy="Aggressive Momentum V1",
                current_prices={"AAPL": 100.0},
            )
            valuation = store.valuation_summary({"AAPL": 100.0, "TSLA": 100.0})
            store.close()

        self.assertLessEqual(valuation["Gross Exposure"], 100_000.0)
        self.assertEqual(valuation["Gross Exposure"], 100_000.0)
        self.assertEqual(valuation["Remaining Capacity"], 0.0)

    def test_40k_short_leaves_only_60k_remaining_capacity(self):
        """Example 1: Start=$100,000, Short=$40,000, Long=$0 -> $60,000 left."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "portfolio.sqlite3"
            store = PaperPortfolioStore(db_path, starting_cash=100_000.0)

            store.open_position(
                ticker="TSLA",
                direction=SHORT,
                entry_price=200.0,
                allocated_capital=40_000.0,
                strategy="Aggressive Momentum V1",
            )
            remaining_capacity = store.get_remaining_capacity({"TSLA": 200.0})
            store.close()

        self.assertEqual(remaining_capacity, 60_000.0)

    def test_70k_long_and_30k_short_leaves_zero_remaining_capacity(self):
        """Example 2: Long=$70,000, Short=$30,000 -> $0 remaining capacity."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "portfolio.sqlite3"
            store = PaperPortfolioStore(db_path, starting_cash=100_000.0)

            store.open_position(
                ticker="AAPL",
                direction=LONG,
                entry_price=100.0,
                allocated_capital=70_000.0,
                strategy="Baseline",
            )
            store.open_position(
                ticker="TSLA",
                direction=SHORT,
                entry_price=100.0,
                allocated_capital=30_000.0,
                strategy="Aggressive Momentum V1",
                current_prices={"AAPL": 100.0},
            )
            remaining_capacity = store.get_remaining_capacity(
                {"AAPL": 100.0, "TSLA": 100.0}
            )
            store.close()

        self.assertEqual(remaining_capacity, 0.0)

    def test_60k_long_then_50k_short_is_rejected_gross_would_be_110k(self):
        """Example 3: Long=$60,000 fits (remaining=$40,000); a further
        $50,000 SHORT must be rejected because gross exposure would become
        $110,000 > the $100,000 starting capital."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "portfolio.sqlite3"
            store = PaperPortfolioStore(db_path, starting_cash=100_000.0)

            store.open_position(
                ticker="AAPL",
                direction=LONG,
                entry_price=100.0,
                allocated_capital=60_000.0,
                strategy="Baseline",
            )

            with self.assertRaises(ValueError):
                store.open_position(
                    ticker="TSLA",
                    direction=SHORT,
                    entry_price=100.0,
                    allocated_capital=50_000.0,
                    strategy="Aggressive Momentum V1",
                    current_prices={"AAPL": 100.0},
                )

            # The rejected attempt must not have partially recorded anything.
            open_positions = store.list_open_positions()
            store.close()

        self.assertEqual(len(open_positions), 1)
        self.assertEqual(open_positions[0].ticker, "AAPL")

    def test_attempt_to_exceed_100k_gross_exposure_is_rejected_for_either_direction(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "portfolio.sqlite3"
            store = PaperPortfolioStore(db_path, starting_cash=100_000.0)

            store.open_position(
                ticker="TSLA",
                direction=SHORT,
                entry_price=200.0,
                allocated_capital=40_000.0,
                strategy="Aggressive Momentum V1",
            )
            # $60,000 remains; a $70,000 LONG must be rejected too - the cap
            # applies uniformly, not just to shorts stacked on longs.
            with self.assertRaises(ValueError):
                store.open_position(
                    ticker="AAPL",
                    direction=LONG,
                    entry_price=100.0,
                    allocated_capital=70_000.0,
                    strategy="Baseline",
                    current_prices={"TSLA": 200.0},
                )
            self.assertEqual(store.get_cash(), 100_000.0)
            store.close()

    def test_persistence_reload_preserves_the_same_capacity_calculation(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "portfolio.sqlite3"

            store = PaperPortfolioStore(db_path, starting_cash=100_000.0)
            store.open_position(
                ticker="AAPL",
                direction=LONG,
                entry_price=100.0,
                allocated_capital=60_000.0,
                strategy="Baseline",
            )
            store.open_position(
                ticker="TSLA",
                direction=SHORT,
                entry_price=200.0,
                allocated_capital=30_000.0,
                strategy="Aggressive Momentum V1",
                current_prices={"AAPL": 100.0},
            )
            current_prices = {"AAPL": 110.0, "TSLA": 180.0}
            valuation_before_restart = store.valuation_summary(current_prices)
            capacity_before_restart = store.get_remaining_capacity(current_prices)
            store.close()  # simulate app shutdown

            reopened_store = PaperPortfolioStore(db_path, starting_cash=100_000.0)
            valuation_after_restart = reopened_store.valuation_summary(current_prices)
            capacity_after_restart = reopened_store.get_remaining_capacity(current_prices)
            reopened_store.close()

        self.assertEqual(capacity_before_restart, capacity_after_restart)
        for key in (
            "Starting Capital",
            "Current Long Exposure",
            "Current Short Exposure",
            "Gross Exposure",
            "Net Exposure",
            "Remaining Capacity",
        ):
            self.assertAlmostEqual(
                valuation_before_restart[key], valuation_after_restart[key]
            )
        # Concrete numbers: AAPL 600 shares * $110 = $66,000 long exposure;
        # TSLA 150 shares short * $180 = $27,000 short exposure.
        self.assertAlmostEqual(valuation_after_restart["Current Long Exposure"], 66_000.0)
        self.assertAlmostEqual(valuation_after_restart["Current Short Exposure"], 27_000.0)
        self.assertAlmostEqual(valuation_after_restart["Gross Exposure"], 93_000.0)
        self.assertAlmostEqual(valuation_after_restart["Remaining Capacity"], 7_000.0)


if __name__ == "__main__":
    unittest.main()
