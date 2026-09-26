"""Research entry points load without network requests or a live account."""
from pathlib import Path
import unittest

from streamlit.testing.v1 import AppTest


class ResearchUISmokeTests(unittest.TestCase):
    def test_benchmark_page_loads_and_preserves_custom_test_count(self):
        root = Path(__file__).resolve().parents[1]
        app = AppTest.from_file(str(root / "app" / "ml_benchmark_ui.py"), default_timeout=30).run()
        self.assertFalse(app.exception)
        count = next(widget for widget in app.number_input if widget.label == "Number of Tests")
        count.set_value(2)
        app.run()
        self.assertFalse(app.exception)
        self.assertEqual(next(widget.value for widget in app.number_input if widget.label == "Number of Tests"), 2)


if __name__ == "__main__":
    unittest.main()
