import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from paper_trading import storage
from paper_trading.plan_store import (
    DROPPED_CANDIDATE_NOTE,
    DuplicateRecommendationError,
    STATUS_DISMISSED,
    STATUS_EXECUTED,
    STATUS_OPEN,
    StalePlanError,
    compare_plans,
    detect_plan_staleness,
    dismiss_recommendation,
    execute_recommendation,
    list_saved_plans,
    load_latest_plan,
    load_plan,
    previous_plan,
    save_plan,
)
from paper_trading.prices import SOURCE_LABEL
from paper_trading.trading_plan import KEEP, REVIEW_EXIT


def _account(cash=100_000.0, starting=100_000.0, realized=0.0):
    return {"Cash": cash, "Starting Capital": starting, "Realized P&L": realized}


def _buy(ticker="AAPL", allocation=20_000.0, price=100.0, **overrides):
    row = {
        "Ticker": ticker,
        "Signal": "BUY",
        "Score": 1.5,
        "Reason": f"{ticker} buy",
        "Price": price,
        "Price As Of": "2026-09-22",
        "Quantity": allocation / price,
        "Allocation": allocation,
    }
    row.update(overrides)
    return row


def _holding(
    ticker="MSFT",
    action=KEEP,
    signal="WAIT",
    position_id=1,
    quantity=10.0,
    allocation=1_000.0,
    **overrides,
):
    row = {
        "id": position_id,
        "ticker": ticker,
        "direction": "LONG",
        "Action": action,
        "Signal": signal,
        "Score": 0.4,
        "Reason": f"{ticker} {action}",
        "Price": 100.0,
        "Price As Of": "2026-09-22",
        "quantity": quantity,
        "allocated_capital": allocation,
    }
    row.update(overrides)
    return row


def _plan(**overrides):
    plan = {
        "mode": "Baseline",
        "strategy_name": "Baseline",
        "regime_name": "SIDEWAYS",
        "strategy_reason": "test plan",
        "price_session_start": "2026-09-21",
        "price_session_end": "2026-09-22",
        "price_source": SOURCE_LABEL,
        "cash_after": 80_000.0,
        "remaining_capacity_before": 100_000.0,
        "remaining_capacity_after": 80_000.0,
        "no_purchase_explanation": None,
        "data_warnings": ["WBA: provider rows missing"],
        "buys": [_buy()],
        "holdings": [],
        "skipped_buys": [],
        "unavailable_universe": ["ATVI"],
    }
    plan.update(overrides)
    return plan


def _snapshot(tickers, price=100.0):
    quotes = {
        ticker: {
            "price": price,
            "as_of": "2026-09-22T00:00:00+00:00",
            "source_label": SOURCE_LABEL,
        }
        for ticker in tickers
    }
    return {
        "quotes": quotes,
        "prices": {ticker: price for ticker in tickers},
        "source_label": SOURCE_LABEL,
    }


class PlanStoreTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_path = Path(self._tmp.name) / "paper_portfolio.sqlite3"
        storage.initialize_storage(self.db_path)

    def tearDown(self):
        self._tmp.cleanup()

    def _save(self, plan=None, account=None, positions=None):
        return save_plan(
            plan or _plan(),
            account or _account(),
            positions or [],
            db_path=self.db_path,
        )

    def test_save_creates_a_new_version_instead_of_overwriting(self):
        first = self._save(_plan(buys=[_buy("AAPL")]))
        second = self._save(_plan(buys=[_buy("MSFT")], strategy_name="Momentum V2"))
        plans = list_saved_plans(self.db_path)
        self.assertEqual([item["id"] for item in plans], [second, first])
        self.assertEqual(load_plan(first, self.db_path)["buys"][0]["Ticker"], "AAPL")
        self.assertEqual(load_latest_plan(self.db_path)["strategy_name"], "Momentum V2")
        self.assertEqual(load_latest_plan(self.db_path)["buys"][0]["Ticker"], "MSFT")

    def test_latest_plan_reloads_after_reopen(self):
        plan_id = self._save(
            _plan(
                buys=[_buy("NVDA", allocation=15_000.0, price=150.0)],
                data_warnings=["NVDA: ok"],
            )
        )
        reopened = load_latest_plan(self.db_path)
        self.assertEqual(reopened["id"], plan_id)
        self.assertEqual(reopened["buys"][0]["Ticker"], "NVDA")
        self.assertAlmostEqual(reopened["buys"][0]["Allocation"], 15_000.0)
        self.assertAlmostEqual(reopened["buys"][0]["Quantity"], 100.0)
        self.assertEqual(reopened["data_warnings"], ["NVDA: ok", "ATVI: price history unavailable; not treated as a sell."])
        self.assertEqual(reopened["account_snapshot"]["Cash"], 100_000.0)
        self.assertEqual(reopened["price_session_end"], "2026-09-22")

    def test_compare_plans_flags_new_dropped_and_signal_changes(self):
        first = self._save(
            _plan(
                buys=[_buy("AAPL"), _buy("MSFT")],
                holdings=[_holding("AMD", action=KEEP, signal="BUY", position_id=7)],
            )
        )
        second = self._save(
            _plan(
                buys=[_buy("MSFT"), _buy("NVDA")],
                holdings=[
                    _holding("AMD", action=REVIEW_EXIT, signal="AVOID", position_id=7)
                ],
            )
        )
        diff = compare_plans(
            previous_plan(second, self.db_path),
            load_plan(second, self.db_path),
        )
        self.assertEqual([row["Ticker"] for row in diff["new_candidates"]], ["NVDA"])
        self.assertEqual([row["Ticker"] for row in diff["dropped_candidates"]], ["AAPL"])
        self.assertEqual(diff["signal_changes"][0]["Ticker"], "AMD")
        self.assertEqual(diff["signal_changes"][0]["Previous Action"], KEEP)
        self.assertEqual(diff["signal_changes"][0]["Current Action"], REVIEW_EXIT)
        self.assertIn("not an instruction to sell", DROPPED_CANDIDATE_NOTE)
        self.assertEqual(previous_plan(first, self.db_path), None)

    def test_execute_buy_links_trade_and_marks_plan_stale(self):
        plan_id = self._save()
        recommendation_id = load_plan(plan_id, self.db_path)["buys"][0]["recommendation_id"]
        result = execute_recommendation(
            recommendation_id, _snapshot, db_path=self.db_path
        )
        self.assertEqual(result["trade_kind"], "OPEN")
        positions = storage.get_open_positions(self.db_path)
        self.assertEqual(len(positions), 1)
        self.assertEqual(positions[0]["id"], result["trade_id"])
        self.assertEqual(positions[0]["ticker"], "AAPL")
        self.assertIn(f"recommendation #{recommendation_id}", positions[0]["reason"])
        saved = load_plan(plan_id, self.db_path)
        self.assertEqual(saved["buys"][0]["status"], STATUS_EXECUTED)
        self.assertEqual(saved["buys"][0]["executed_trade_id"], result["trade_id"])
        self.assertTrue(saved["needs_regeneration"])
        stale, reason = detect_plan_staleness(
            saved, storage.get_account_state(self.db_path), positions
        )
        self.assertTrue(stale)
        self.assertIn("snapshot", (reason or "").lower())

    def test_duplicate_execute_is_rejected(self):
        plan_id = self._save()
        recommendation_id = load_plan(plan_id, self.db_path)["buys"][0]["recommendation_id"]
        execute_recommendation(recommendation_id, _snapshot, db_path=self.db_path)
        with self.assertRaises(DuplicateRecommendationError):
            execute_recommendation(recommendation_id, _snapshot, db_path=self.db_path)
        self.assertEqual(len(storage.get_open_positions(self.db_path)), 1)

    def test_second_recommendation_on_stale_plan_is_blocked(self):
        plan_id = self._save(_plan(buys=[_buy("AAPL"), _buy("MSFT")]))
        loaded = load_plan(plan_id, self.db_path)
        execute_recommendation(
            loaded["buys"][0]["recommendation_id"], _snapshot, db_path=self.db_path
        )
        with self.assertRaises(StalePlanError):
            execute_recommendation(
                loaded["buys"][1]["recommendation_id"], _snapshot, db_path=self.db_path
            )
        self.assertEqual(len(storage.get_open_positions(self.db_path)), 1)

    def test_dismiss_is_not_a_trade(self):
        plan_id = self._save()
        recommendation_id = load_plan(plan_id, self.db_path)["buys"][0]["recommendation_id"]
        dismiss_recommendation(recommendation_id, "skip today", db_path=self.db_path)
        saved = load_plan(plan_id, self.db_path)
        self.assertEqual(saved["buys"][0]["status"], STATUS_DISMISSED)
        self.assertEqual(saved["buys"][0]["dismissal_note"], "skip today")
        self.assertFalse(saved["needs_regeneration"])
        self.assertEqual(storage.get_open_positions(self.db_path), [])
        with self.assertRaises(DuplicateRecommendationError):
            dismiss_recommendation(recommendation_id, db_path=self.db_path)

    def test_review_exit_closes_the_linked_position(self):
        position_id = storage.open_position(
            ticker="AMD",
            entry_price=50.0,
            allocated_capital=5_000.0,
            strategy="Baseline",
            reason="seed",
            current_prices={"AMD": 50.0},
            db_path=self.db_path,
        )
        account = storage.get_account_state(self.db_path)
        holdings = storage.get_open_positions(self.db_path)
        plan_id = self._save(
            _plan(
                buys=[],
                holdings=[
                    _holding(
                        "AMD",
                        action=REVIEW_EXIT,
                        signal="AVOID",
                        position_id=position_id,
                        quantity=100.0,
                        allocation=5_000.0,
                    )
                ],
            ),
            account=account,
            positions=holdings,
        )
        recommendation_id = load_plan(plan_id, self.db_path)["holdings"][0][
            "recommendation_id"
        ]
        result = execute_recommendation(
            recommendation_id, lambda tickers: _snapshot(tickers, price=55.0), db_path=self.db_path
        )
        self.assertEqual(result["trade_kind"], "CLOSE")
        self.assertEqual(storage.get_open_positions(self.db_path), [])
        closed = storage.get_closed_trades(self.db_path)
        self.assertEqual(closed[0]["id"], result["trade_id"])
        self.assertEqual(closed[0]["ticker"], "AMD")

    def test_keep_and_unavailable_are_not_trades(self):
        plan_id = self._save(
            _plan(
                buys=[],
                holdings=[_holding("AAPL", action=KEEP, signal="BUY", position_id=9)],
            )
        )
        recommendation_id = load_plan(plan_id, self.db_path)["holdings"][0][
            "recommendation_id"
        ]
        with self.assertRaises(ValueError):
            execute_recommendation(recommendation_id, _snapshot, db_path=self.db_path)
        self.assertEqual(
            load_plan(plan_id, self.db_path)["holdings"][0]["status"], STATUS_OPEN
        )


class LauncherTests(unittest.TestCase):
    def test_windows_launcher_uses_project_venv(self):
        launcher = Path(__file__).resolve().parents[1] / "Launch-Paper-Trading.bat"
        text = launcher.read_text(encoding="utf-8")
        self.assertIn("%~dp0", text)
        self.assertIn(r".venv\Scripts\python.exe", text)
        self.assertIn(r"app\competition_dashboard.py", text)


if __name__ == "__main__":
    unittest.main()
