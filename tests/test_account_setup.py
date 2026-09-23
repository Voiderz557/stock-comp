import hashlib
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from paper_trading import storage
from paper_trading.account_setup import (
    AccountSetupError,
    apply_account_snapshot,
    parse_holdings_text,
    preview_account_snapshot,
)
from paper_trading.plan_store import detect_plan_staleness, list_saved_plans, load_latest_plan, save_plan
from paper_trading.prices import SOURCE_LABEL
from paper_trading.trading_plan import AUTOMATIC_MODE


def _digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class AccountSetupTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_path = Path(self._tmp.name) / "paper_portfolio.sqlite3"
        storage.initialize_storage(self.db_path)

    def tearDown(self):
        self._tmp.cleanup()

    def _preview(self, cash=80_000.0, holdings=None, starting=100_000.0, as_of="2026-09-23"):
        return preview_account_snapshot(
            starting,
            cash,
            holdings
            or [
                {
                    "ticker": "AAPL",
                    "direction": "LONG",
                    "quantity": 100,
                    "average_price": 200,
                }
            ],
            as_of,
            db_path=self.db_path,
        )

    def test_parse_rejects_duplicate_ticker_direction(self):
        with self.assertRaises(AccountSetupError):
            parse_holdings_text("AAPL, LONG, 10, 100\nAAPL, LONG, 5, 110")

    def test_cost_basis_is_accepted_and_identity_is_required(self):
        preview = self._preview(
            cash=80_000.0,
            holdings=parse_holdings_text("AAPL, LONG, 100, , 20000"),
        )
        self.assertTrue(preview["can_save"])
        self.assertAlmostEqual(preview["holdings"][0]["entry_price"], 200.0)
        self.assertAlmostEqual(preview["allocated_capital"], 20_000.0)
        self.assertEqual(preview["preserved_realized_pnl"], 0.0)
        broken = self._preview(cash=50_000.0)
        self.assertFalse(broken["can_save"])
        self.assertTrue(broken["missing_information"])
        self.assertIn("will not invent", broken["missing_information"][0])

    def test_save_backs_up_replaces_opens_and_keeps_closed_history(self):
        storage.open_position(
            ticker="MSFT",
            entry_price=100.0,
            allocated_capital=1_000.0,
            current_prices={"MSFT": 100.0},
            db_path=self.db_path,
        )
        storage.close_position(
            storage.get_open_positions(self.db_path)[0]["id"],
            exit_price=110.0,
            db_path=self.db_path,
        )
        closed_before = storage.get_closed_trades(self.db_path)
        self.assertEqual(len(closed_before), 1)
        realized = storage.get_account_state(self.db_path)["Realized P&L"]
        # cash + AAPL 20k = starting + realized
        cash = 100_000.0 + realized - 20_000.0
        preview = self._preview(cash=cash)
        before = _digest(self.db_path)
        result = apply_account_snapshot(preview, db_path=self.db_path)
        self.assertTrue(result["applied"])
        self.assertTrue(Path(result["backup"]).is_file())
        self.assertNotEqual(_digest(self.db_path), before)
        self.assertEqual(_digest(result["backup"]), before)
        account = storage.get_account_state(self.db_path)
        opens = storage.get_open_positions(self.db_path)
        self.assertAlmostEqual(account["Cash"], cash)
        self.assertEqual(account["Snapshot As Of"], "2026-09-23")
        self.assertEqual(len(opens), 1)
        self.assertEqual(opens[0]["ticker"], "AAPL")
        self.assertEqual(opens[0]["strategy"], storage.SNAPSHOT_STRATEGY)
        self.assertEqual(storage.get_closed_trades(self.db_path), closed_before)
        self.assertAlmostEqual(account["Realized P&L"], realized)
        second = apply_account_snapshot(self._preview(cash=cash), db_path=self.db_path)
        self.assertFalse(second["applied"])
        self.assertTrue(second["already_matches"])
        self.assertEqual(len(storage.get_open_positions(self.db_path)), 1)

    def test_apply_invalidates_previous_plan_and_survives_reopen(self):
        save_plan(
            {
                "mode": "Baseline",
                "strategy_name": "Baseline",
                "regime_name": "SIDEWAYS",
                "strategy_reason": "before snapshot",
                "price_session_start": "2026-09-22",
                "price_session_end": "2026-09-22",
                "price_source": SOURCE_LABEL,
                "cash_after": 100_000.0,
                "remaining_capacity_before": 100_000.0,
                "remaining_capacity_after": 100_000.0,
                "no_purchase_explanation": None,
                "data_warnings": [],
                "buys": [],
                "holdings": [],
                "skipped_buys": [],
                "unavailable_universe": [],
            },
            storage.get_account_state(self.db_path),
            [],
            db_path=self.db_path,
        )
        apply_account_snapshot(self._preview(), db_path=self.db_path)
        reopened_account = storage.get_account_state(self.db_path)
        reopened_opens = storage.get_open_positions(self.db_path)
        self.assertEqual(reopened_opens[0]["ticker"], "AAPL")
        latest = load_latest_plan(self.db_path)
        stale, reason = detect_plan_staleness(latest, reopened_account, reopened_opens)
        self.assertTrue(stale)
        self.assertIn("snapshot", (latest.get("stale_reason") or reason or "").lower())

    def test_build_plan_receives_entered_holdings_and_cash(self):
        apply_account_snapshot(self._preview(), db_path=self.db_path)
        captured = {}

        def fake_builder(mode, account, holdings, **kwargs):
            captured["account"] = account
            captured["holdings"] = holdings
            return {
                "mode": mode,
                "strategy_name": "Baseline",
                "strategy_reason": "Mocked selector reason.",
                "regime_name": "SIDEWAYS",
                "recommendation": None,
                "price_source": SOURCE_LABEL,
                "buys": [
                    {
                        "Ticker": "NVDA",
                        "Signal": "BUY",
                        "Score": 1.0,
                        "Reason": "algorithm score on current cash/holdings",
                        "Price": 100.0,
                        "Price As Of": "2026-09-23",
                        "Quantity": 10.0,
                        "Allocation": 1_000.0,
                    }
                ],
                "skipped_buys": [{"Ticker": "META", "Reason": "capacity"}],
                "holdings": [
                    {
                        "id": holdings[0]["id"],
                        "ticker": "AAPL",
                        "direction": "LONG",
                        "Action": "KEEP",
                        "Signal": "WAIT",
                        "Score": 0.2,
                        "Reason": "existing snapshot holding",
                        "Price": 200.0,
                        "Price As Of": "2026-09-23",
                        "quantity": 100.0,
                        "allocated_capital": 20_000.0,
                    }
                ],
                "cash_before": account["Cash"],
                "cash_after": account["Cash"],
                "remaining_capacity_before": None,
                "remaining_capacity_after": None,
                "no_purchase_explanation": None,
                "unavailable_universe": ["ATVI"],
                "data_warnings": ["ATVI: price history unavailable; not treated as a sell."],
            }

        account = storage.get_account_state(self.db_path)
        opens = storage.get_open_positions(self.db_path)
        plan = fake_builder(AUTOMATIC_MODE, account, opens)
        save_plan(plan, account, opens, db_path=self.db_path)
        self.assertAlmostEqual(captured["account"]["Cash"], 80_000.0)
        self.assertEqual(captured["holdings"][0]["ticker"], "AAPL")
        saved = load_latest_plan(self.db_path)
        self.assertEqual(saved["buys"][0]["Ticker"], "NVDA")
        self.assertEqual(saved["holdings"][0]["ticker"], "AAPL")
        self.assertEqual(saved["data_warnings"][0].split(":")[0], "ATVI")
        self.assertEqual(len(list_saved_plans(self.db_path)), 1)


if __name__ == "__main__":
    unittest.main()
