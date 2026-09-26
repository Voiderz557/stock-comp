import unittest

import pandas as pd

from benchmarking.recover_data import refresh_bounds, relevant_failure_segments, validate_refresh


class RecoverySafetyTests(unittest.TestCase):
    def test_weekend_failure_does_not_block_required_trading_history(self):
        failures = [{
            "Ticker": "CRWD", "Provider": "yfinance",
            "Requested Start": "2026-09-05", "Requested End": "2026-09-05",
        }]
        required = pd.to_datetime(["2023-03-23", "2023-03-24"])
        self.assertEqual(
            relevant_failure_segments(
                failures, "CRWD", pd.Timestamp("2023-01-01"),
                pd.Timestamp("2026-09-06"), required,
            ),
            [],
        )

    def test_failure_covering_required_session_is_honored(self):
        failure = {
            "Ticker": "ATVI", "Provider": "yfinance",
            "Requested Start": "2023-03-23", "Requested End": "2023-05-15",
        }
        result = relevant_failure_segments(
            [failure], "ATVI", pd.Timestamp("2020-01-01"),
            pd.Timestamp("2023-10-14"), pd.to_datetime(["2023-03-23"]),
        )
        self.assertEqual(len(result), 1)

    def test_refresh_rejects_loss_of_existing_rows(self):
        index = pd.to_datetime(["2023-03-22", "2023-03-23"])
        old = self._prices(index)
        fresh = self._prices(index[:1])
        with self.assertRaisesRegex(ValueError, "omits"):
            validate_refresh(old, fresh, index)

    def test_refresh_bounds_include_required_history_before_late_cached_rows(self):
        old = self._prices(pd.to_datetime(["2026-07-17", "2026-08-10"]))
        required = pd.to_datetime(["2023-01-03", "2025-12-31"])
        start, end = refresh_bounds(old, required)
        self.assertEqual(start, pd.Timestamp("2020-04-29"))
        self.assertEqual(end, pd.Timestamp("2026-08-11"))

    @staticmethod
    def _prices(index):
        return pd.DataFrame(
            {"Open": 10.0, "High": 11.0, "Low": 9.0, "Close": 10.0, "Volume": 100.0},
            index=index,
        )


if __name__ == "__main__":
    unittest.main()
