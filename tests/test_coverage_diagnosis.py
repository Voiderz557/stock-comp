import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from data.coverage_diagnosis import (
    COVERED,
    INTERNAL_GAP,
    MISSING_REQUIRED_HISTORY,
    PRE_LISTING_WARMUP,
    classify_coverage_gap,
    diagnose_ticker_coverage,
)
from data.market_data import load_market_data
from tests.test_backtest import cache_frame


class CoverageClassificationTests(unittest.TestCase):
    def test_ceg_pre_listing_window_is_warmup_shortage(self):
        self.assertEqual(
            classify_coverage_gap("CEG", "2020-04-29", "2022-01-18"),
            PRE_LISTING_WARMUP,
        )

    def test_dash_gehc_gfs_pre_listing_windows_are_warmup_shortages(self):
        self.assertEqual(
            classify_coverage_gap("DASH", "2020-04-29", "2020-12-08"),
            PRE_LISTING_WARMUP,
        )
        self.assertEqual(
            classify_coverage_gap("GEHC", "2020-04-29", "2022-12-14"),
            PRE_LISTING_WARMUP,
        )
        self.assertEqual(
            classify_coverage_gap("GFS", "2020-04-29", "2021-10-27"),
            PRE_LISTING_WARMUP,
        )

    def test_anss_listed_empty_frame_is_missing_required_history(self):
        empty = pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
        empty.index = pd.DatetimeIndex([])
        self.assertEqual(
            classify_coverage_gap("ANSS", "2024-10-16", "2024-11-16", empty),
            MISSING_REQUIRED_HISTORY,
        )

    def test_ea_listed_gap_is_missing_required_history(self):
        frame = cache_frame("2026-07-17", "2026-08-10")
        self.assertEqual(
            classify_coverage_gap("EA", "2024-10-16", "2024-11-16", frame),
            MISSING_REQUIRED_HISTORY,
        )

    def test_internal_gap_when_some_required_rows_exist(self):
        frame = cache_frame("2024-10-16", "2024-10-20")
        rows = diagnose_ticker_coverage(
            "CEG",
            [(pd.Timestamp("2024-10-16"), pd.Timestamp("2024-11-16"))],
            price_data={"CEG": frame},
        )
        self.assertEqual(rows[0]["Classification"], INTERNAL_GAP)
        self.assertGreater(rows[0]["Rows In Required Range"], 0)

    def test_listed_window_with_full_cache_is_covered(self):
        frame = cache_frame("2024-10-16", "2024-11-16")
        self.assertEqual(
            classify_coverage_gap(
                "CEG", "2024-10-16", "2024-11-16", frame
            ),
            COVERED,
        )


class PreListingFailureIsNotRequiredTests(unittest.TestCase):
    def test_cached_pre_listing_miss_does_not_invalidate_listed_ceg(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as cache_dir:
            cache_dir = Path(cache_dir)
            listed = cache_frame("2022-01-19", "2024-11-20")
            listed.to_parquet(cache_dir / "CEG.parquet")
            (cache_dir / "coverage.json").write_text(
                json.dumps(
                    {
                        "CEG": {
                            "start": "2022-01-19",
                            "end_exclusive": "2024-11-21",
                        }
                    }
                ),
                encoding="utf-8",
            )
            (cache_dir / "data_failures.json").write_text(
                json.dumps(
                    [
                        {
                            "Ticker": "CEG",
                            "Requested Start": "2020-04-29",
                            "Requested End": "2022-01-18",
                            "Data Status": "MISSING DATA FROM PROVIDER",
                            "Provider": "yfinance",
                            "Reason": "yfinance returned no rows",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            with patch(
                "data.market_data._download_range", return_value=pd.DataFrame()
            ) as download:
                data, report = load_market_data(
                    ["CEG"],
                    "2020-04-29",
                    "2024-11-16",
                    cache_dir=cache_dir,
                    required_ranges={
                        "CEG": [
                            (pd.Timestamp("2024-10-16"), pd.Timestamp("2024-11-16"))
                        ]
                    },
                    secondary_providers=[],
                )
        download.assert_not_called()
        self.assertFalse(data["CEG"].empty)
        self.assertEqual(report["Data Source Failures"], [])
        self.assertEqual(report["Unavailable Valid Constituents"], [])
