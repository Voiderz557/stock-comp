import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from ai.provider import AnalysisResult
from paper_trading import storage
from paper_trading.plan_store import list_saved_plans, load_latest_plan, save_plan
from paper_trading.prices import SOURCE_LABEL
from paper_trading.trading_plan import AUTOMATIC_MODE


DASHBOARD_PATH = Path(__file__).resolve().parents[1] / "app" / "competition_dashboard.py"


class DashboardSmokeTests(unittest.TestCase):
    def test_dashboard_loads_against_temporary_database(self):
        with TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "paper_portfolio.sqlite3")
            with patch("paper_trading.storage.DEFAULT_DB_PATH", db_path):
                app = AppTest.from_file(str(DASHBOARD_PATH))
                app.run(timeout=60)
                self.assertFalse(app.exception, msg=repr(app.exception))

                titles = [element.value for element in app.title]
                self.assertIn("Paper Trading Competition Dashboard", titles)

                captions = [str(element.value) for element in app.caption]
                self.assertTrue(any("not a real-time" in value.lower() for value in captions))
                self.assertTrue(any("informational only" in value.lower() for value in captions))
                self.assertTrue(any("daily close" in value.lower() for value in captions))
                self.assertFalse(any("real-time quote" in value.lower() and "not" not in value.lower() for value in captions))

                subheaders = [element.value for element in app.subheader]
                self.assertIn("Ledger audit", subheaders)
                self.assertIn("Latest saved plan", subheaders)
                self.assertTrue(
                    any("Saved portfolio and plans" in value for value in captions)
                )

                successes = [str(element.value) for element in app.success]
                self.assertTrue(
                    any("Accounting identities passed" in value for value in successes),
                    msg=f"Expected a passing ledger audit on an empty temp account; got {successes!r}",
                )

                account = storage.get_account_state(db_path)
                self.assertAlmostEqual(account["Cash"], 100_000.0)
                self.assertAlmostEqual(account["Starting Capital"], 100_000.0)
                self.assertEqual(storage.get_open_positions(db_path), [])

                headers = [element.value for element in app.header]
                self.assertNotIn("AI analysis", headers)
                self.assertIn("Set up / update competition account", headers)
                self.assertIn("Today's Trading Plan", headers)
                subheaders = [element.value for element in app.subheader]
                self.assertIn("Latest saved plan", subheaders)
                infos = [str(element.value) for element in app.info]
                self.assertTrue(
                    any("No saved plan yet" in value for value in infos),
                    msg=f"Expected an empty saved-plan state; got {infos!r}",
                )
                select_labels = [str(getattr(element, "label", "")) for element in app.selectbox]
                self.assertTrue(
                    any("Strategy for today's plan" in label for label in select_labels),
                    msg=f"Expected a plan strategy selector; got {select_labels!r}",
                )
                button_labels = [str(getattr(element, "label", element)) for element in app.button]
                self.assertTrue(
                    any("Build today's trading plan" in label for label in button_labels),
                    msg=f"Expected a plan button; got {button_labels!r}",
                )
                self.assertFalse(
                    any("Analyze current state" in label for label in button_labels),
                    msg=f"AI should stay hidden by default; got {button_labels!r}",
                )
                self.assertTrue(
                    any("Review snapshot" in label for label in button_labels),
                    msg=f"Expected a snapshot review button; got {button_labels!r}",
                )

    def test_plan_button_is_recommendation_only_when_mocked(self):
        fake_plan = {
            "mode": AUTOMATIC_MODE,
            "strategy_name": "Baseline",
            "strategy_reason": "Mocked selector reason.",
            "regime_name": "SIDEWAYS",
            "recommendation": None,
            "price_source": SOURCE_LABEL,
            "buys": [],
            "skipped_buys": [],
            "holdings": [],
            "cash_before": 100_000.0,
            "cash_after": 100_000.0,
            "remaining_capacity_before": 100_000.0,
            "remaining_capacity_after": 100_000.0,
            "no_purchase_explanation": "No purchases qualify: mocked empty scan.",
            "unavailable_universe": [],
            "ranked_buy_count": 0,
        }
        with TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "paper_portfolio.sqlite3")
            with patch("paper_trading.storage.DEFAULT_DB_PATH", db_path), patch(
                "paper_trading.trading_plan.load_and_build_trading_plan",
                return_value=fake_plan,
            ) as builder:
                app = AppTest.from_file(str(DASHBOARD_PATH))
                app.run(timeout=60)
                self.assertFalse(app.exception, msg=repr(app.exception))
                plan_buttons = [
                    button
                    for button in app.button
                    if "Build today's trading plan" in str(getattr(button, "label", button))
                ]
                self.assertEqual(len(plan_buttons), 1)
                plan_buttons[0].click()
                app.run(timeout=60)
                self.assertFalse(app.exception, msg=repr(app.exception))
                builder.assert_called_once()
                infos = [str(element.value) for element in app.info]
                self.assertTrue(
                    any("No purchases qualify" in value for value in infos),
                    msg=f"Expected mocked empty-plan explanation; got {infos!r}",
                )
                account = storage.get_account_state(db_path)
                self.assertAlmostEqual(account["Cash"], 100_000.0)
                self.assertEqual(storage.get_open_positions(db_path), [])
                saved = list_saved_plans(db_path)
                self.assertEqual(len(saved), 1)
                self.assertEqual(load_latest_plan(db_path)["strategy_name"], "Baseline")
                select_labels = [str(getattr(element, "label", "")) for element in app.selectbox]
                self.assertTrue(
                    any("Saved plans by date" in label for label in select_labels),
                    msg=f"Expected a saved-plan history selector; got {select_labels!r}",
                )

    def test_dashboard_restores_latest_saved_plan_on_restart(self):
        persisted_plan = {
            "mode": "Baseline",
            "strategy_name": "Baseline",
            "strategy_reason": "Restored after restart.",
            "regime_name": "SIDEWAYS",
            "price_session_start": "2026-09-22",
            "price_session_end": "2026-09-22",
            "price_source": SOURCE_LABEL,
            "cash_after": 80_000.0,
            "remaining_capacity_before": 100_000.0,
            "remaining_capacity_after": 80_000.0,
            "no_purchase_explanation": None,
            "data_warnings": [],
            "buys": [
                {
                    "Ticker": "AAPL",
                    "Signal": "BUY",
                    "Score": 1.0,
                    "Reason": "restored buy",
                    "Price": 100.0,
                    "Price As Of": "2026-09-22",
                    "Quantity": 200.0,
                    "Allocation": 20_000.0,
                }
            ],
            "holdings": [],
            "skipped_buys": [],
            "unavailable_universe": [],
        }
        with TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "paper_portfolio.sqlite3")
            with patch("paper_trading.storage.DEFAULT_DB_PATH", db_path):
                storage.initialize_storage(db_path)
                save_plan(
                    persisted_plan,
                    storage.get_account_state(db_path),
                    [],
                    db_path=db_path,
                )
                app = AppTest.from_file(str(DASHBOARD_PATH))
                app.run(timeout=60)
                self.assertFalse(app.exception, msg=repr(app.exception))
                self.assertEqual(load_latest_plan(db_path)["buys"][0]["Ticker"], "AAPL")
                captions = [str(element.value) for element in app.caption]
                self.assertTrue(
                    any("Plan #" in value and "generated" in value for value in captions),
                    msg=f"Expected restored plan age; got {captions!r}",
                )
                self.assertTrue(
                    any("Proposed buys: AAPL" in value for value in captions),
                    msg=f"Expected restored buy on the home screen; got {captions!r}",
                )
                button_labels = [
                    str(getattr(element, "label", element)) for element in app.button
                ]
                self.assertTrue(
                    any("Record paper trade" in label for label in button_labels),
                    msg=f"Expected an explicit record-trade action; got {button_labels!r}",
                )
                self.assertTrue(
                    any("Dismiss recommendation" in label for label in button_labels),
                    msg=f"Expected a dismiss action; got {button_labels!r}",
                )


    def test_analyze_stays_hidden_unless_explicitly_enabled(self):
        fake_result = AnalysisResult(
            ok=True,
            text="Mocked advisory reply.",
            provider="fake",
            model="fake-model",
        )
        with TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "paper_portfolio.sqlite3")
            with patch("paper_trading.storage.DEFAULT_DB_PATH", db_path), patch(
                "ai.ui.analyze_question", return_value=fake_result
            ) as analyze:
                app = AppTest.from_file(str(DASHBOARD_PATH))
                app.run(timeout=60)
                self.assertFalse(app.exception, msg=repr(app.exception))
                analyze.assert_not_called()
                self.assertNotIn("AI analysis", [element.value for element in app.header])

            with patch.dict("os.environ", {"STOCK_COMP_AI_ENABLED": "1"}, clear=False), patch(
                "paper_trading.storage.DEFAULT_DB_PATH", db_path
            ), patch("ai.ui.analyze_question", return_value=fake_result) as analyze:
                app = AppTest.from_file(str(DASHBOARD_PATH))
                app.run(timeout=60)
                self.assertFalse(app.exception, msg=repr(app.exception))
                analyze.assert_not_called()
                self.assertIn("AI analysis", [element.value for element in app.header])
                analyze_buttons = [
                    button
                    for button in app.button
                    if "Analyze current state" in str(getattr(button, "label", button))
                ]
                self.assertEqual(len(analyze_buttons), 1)
                analyze_buttons[0].click()
                app.run(timeout=60)
                self.assertFalse(app.exception, msg=repr(app.exception))
                analyze.assert_called_once()

    def test_manual_snapshot_survives_restart_and_feeds_plan(self):
        from paper_trading.account_setup import apply_account_snapshot, preview_account_snapshot

        fake_plan = {
            "mode": AUTOMATIC_MODE,
            "strategy_name": "Baseline",
            "strategy_reason": "Uses entered holdings and cash.",
            "regime_name": "SIDEWAYS",
            "recommendation": None,
            "price_source": SOURCE_LABEL,
            "buys": [],
            "skipped_buys": [{"Ticker": "META", "Reason": "capacity"}],
            "holdings": [],
            "cash_before": 80_000.0,
            "cash_after": 80_000.0,
            "remaining_capacity_before": None,
            "remaining_capacity_after": None,
            "no_purchase_explanation": "No purchases qualify: mocked empty scan.",
            "unavailable_universe": ["ATVI"],
            "data_warnings": ["ATVI: price history unavailable; not treated as a sell."],
        }
        with TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "paper_portfolio.sqlite3")
            storage.initialize_storage(db_path)
            preview = preview_account_snapshot(
                100_000.0,
                80_000.0,
                [
                    {
                        "ticker": "AAPL",
                        "direction": "LONG",
                        "quantity": 100,
                        "average_price": 200,
                    }
                ],
                "2026-09-23",
                db_path=db_path,
            )
            apply_account_snapshot(preview, db_path=db_path)
            with patch("paper_trading.storage.DEFAULT_DB_PATH", db_path), patch(
                "paper_trading.trading_plan.load_and_build_trading_plan",
                return_value=fake_plan,
            ) as builder:
                app = AppTest.from_file(str(DASHBOARD_PATH))
                app.run(timeout=60)
                self.assertFalse(app.exception, msg=repr(app.exception))
                captions = [str(element.value) for element in app.caption]
                self.assertTrue(
                    any("2026-09-23" in value for value in captions),
                    msg=f"Expected restored snapshot date; got {captions!r}",
                )
                self.assertEqual(storage.get_open_positions(db_path)[0]["ticker"], "AAPL")
                plan_buttons = [
                    button
                    for button in app.button
                    if "Build today's trading plan" in str(getattr(button, "label", button))
                ]
                plan_buttons[0].click()
                app.run(timeout=60)
                self.assertFalse(app.exception, msg=repr(app.exception))
                builder.assert_called_once()
                _mode, account_arg, holdings_arg = builder.call_args.args[:3]
                self.assertAlmostEqual(account_arg["Cash"], 80_000.0)
                self.assertEqual(holdings_arg[0]["ticker"], "AAPL")
                self.assertEqual(load_latest_plan(db_path)["data_warnings"][0].split(":")[0], "ATVI")


if __name__ == "__main__":
    unittest.main()
