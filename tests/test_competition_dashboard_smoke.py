import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from paper_trading import storage


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

                successes = [str(element.value) for element in app.success]
                self.assertTrue(
                    any("Accounting identities passed" in value for value in successes),
                    msg=f"Expected a passing ledger audit on an empty temp account; got {successes!r}",
                )

                account = storage.get_account_state(db_path)
                self.assertAlmostEqual(account["Cash"], 100_000.0)
                self.assertAlmostEqual(account["Starting Capital"], 100_000.0)
                self.assertEqual(storage.get_open_positions(db_path), [])


if __name__ == "__main__":
    unittest.main()
