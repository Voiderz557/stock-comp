import unittest

import numpy as np
import pandas as pd

from market.breadth import compute_market_breadth


def _prices(start, end, periods=230):
    dates = pd.bdate_range("2024-01-01", periods=periods)
    close = np.linspace(start, end, periods)
    return pd.DataFrame({"Close": close}, index=dates)


class MarketBreadthTests(unittest.TestCase):
    def test_breadth_values_and_coverage_are_explicit(self):
        data = {"UP": _prices(100, 150), "DOWN": _prices(150, 100)}
        evaluation_date = data["UP"].index[-1] + pd.Timedelta(days=1)
        result = compute_market_breadth(data, ["UP", "DOWN", "MISSING"], evaluation_date)
        self.assertEqual(result.eligible_count, 2)
        self.assertAlmostEqual(result.coverage_ratio, 2 / 3)
        self.assertAlmostEqual(result.pct_above_ma50, 0.5)
        self.assertAlmostEqual(result.pct_positive_momentum_20d, 0.5)

    def test_future_rows_cannot_change_earlier_breadth(self):
        frame = _prices(100, 150)
        evaluation_date = frame.index[210]
        before = compute_market_breadth({"UP": frame}, ["UP"], evaluation_date)
        changed = frame.copy()
        changed.loc[changed.index >= evaluation_date, "Close"] = 1_000_000
        after = compute_market_breadth({"UP": changed}, ["UP"], evaluation_date)
        self.assertEqual(before, after)

    def test_insufficient_history_is_not_counted_as_bearish(self):
        frame = _prices(100, 110, periods=50)
        result = compute_market_breadth(
            {"NEW": frame}, ["NEW"], frame.index[-1] + pd.Timedelta(days=1)
        )
        self.assertEqual(result.eligible_count, 0)
        self.assertIsNone(result.pct_above_ma200)


if __name__ == "__main__":
    unittest.main()
