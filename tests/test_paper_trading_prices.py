import unittest

import pandas as pd

from paper_trading.prices import (
    SOURCE_LABEL,
    fetch_execution_snapshot,
    latest_daily_close_quote,
    quotes_from_price_frames,
)


def _price_frame(closes, start="2025-01-01"):
    return pd.DataFrame(
        {"Close": closes, "Open": closes, "Volume": [1_000] * len(closes)},
        index=pd.date_range(start, periods=len(closes)),
    )


class DailyCloseQuoteTests(unittest.TestCase):
    def test_extracts_latest_close_and_timestamp(self):
        quote = latest_daily_close_quote(_price_frame([10.0, 11.0, 12.5]))
        self.assertAlmostEqual(quote["price"], 12.5)
        self.assertEqual(quote["source_label"], SOURCE_LABEL)
        self.assertIn("2025-01-03", quote["as_of"])
        self.assertNotIn("real-time", quote["source_label"].lower().replace("not real-time", ""))

    def test_source_label_is_not_realtime(self):
        self.assertIn("not real-time", SOURCE_LABEL)
        self.assertIn("daily close", SOURCE_LABEL)

    def test_empty_frame_returns_none(self):
        self.assertIsNone(latest_daily_close_quote(_price_frame([])))
        self.assertIsNone(latest_daily_close_quote(None))


class ExecutionSnapshotTests(unittest.TestCase):
    def test_requires_every_requested_ticker(self):
        frames = {"AAPL": _price_frame([100.0]), "MSFT": _price_frame([50.0])}
        snapshot = quotes_from_price_frames(frames, ["AAPL", "MSFT"])
        self.assertEqual(snapshot["prices"], {"AAPL": 100.0, "MSFT": 50.0})
        with self.assertRaises(ValueError) as context:
            quotes_from_price_frames(frames, ["AAPL", "TSLA"])
        self.assertIn("TSLA", str(context.exception))
        self.assertIn("daily close", str(context.exception).lower())

    def test_fetch_uses_loader_and_ignores_scan_price(self):
        scan_price = 99.0
        frames = {"AAPL": _price_frame([100.0, 105.0]), "MSFT": _price_frame([40.0])}

        def fake_load(tickers, start, end):
            self.assertEqual(list(tickers), ["AAPL", "MSFT"])
            return {ticker: frames[ticker] for ticker in tickers}, {}

        snapshot = fetch_execution_snapshot(
            ["AAPL", "MSFT"],
            fake_load,
            as_of_date="2025-06-01",
        )
        self.assertAlmostEqual(snapshot["prices"]["AAPL"], 105.0)
        self.assertNotAlmostEqual(snapshot["prices"]["AAPL"], scan_price)
        self.assertEqual(snapshot["source_label"], SOURCE_LABEL)

    def test_invalid_close_is_rejected(self):
        frames = {"AAPL": _price_frame([float("nan")])}
        with self.assertRaises(ValueError):
            quotes_from_price_frames(frames, ["AAPL"])

    def test_empty_ticker_list_does_not_call_loader(self):
        def boom(*args, **kwargs):
            raise AssertionError("loader should not run")

        snapshot = fetch_execution_snapshot([], boom)
        self.assertEqual(snapshot["prices"], {})
