import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import pandas as pd

from benchmarking.import_prices import (
    load_price_file,
    stage_historical_import,
    validate_historical_import,
)


def _prices(index):
    return pd.DataFrame(
        {"Open": 10.0, "High": 11.0, "Low": 9.0, "Close": 10.5, "Volume": 1000.0},
        index=pd.DatetimeIndex(index),
    )


class HistoricalPriceImportTests(unittest.TestCase):
    def test_valid_file_is_staged_with_provenance_and_checksums(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate = root / "candidate"
            candidate.mkdir()
            dates = pd.bdate_range("2023-01-02", "2023-01-10")
            _prices(dates).to_parquet(candidate / "SPY.parquet")
            source = root / "atvi.csv"
            frame = _prices(dates).rename_axis("Date").reset_index()
            frame.to_csv(source, index=False)
            loaded, report = validate_historical_import(
                source, "ATVI", candidate, "Example licensed export",
                "https://example.com/dataset", "adjusted",
                "2023-01-02", "2023-01-10",
            )
            price_path, manifest_path = stage_historical_import(
                loaded, report, root / "staged", project_root=root,
            )
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertTrue(price_path.is_file())
            self.assertEqual(manifest["status"], "VALIDATED_FOR_STAGING_NOT_PROMOTED")
            self.assertEqual(manifest["required_sessions"], len(dates))
            self.assertEqual(len(manifest["input_sha256"]), 64)
            self.assertEqual(len(manifest["staged_parquet_sha256"]), 64)
            self.assertFalse(manifest["adjustment_convention_independently_verified"])

    def test_missing_required_session_is_rejected(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate = root / "candidate"
            candidate.mkdir()
            dates = pd.bdate_range("2023-01-02", "2023-01-10")
            _prices(dates).to_parquet(candidate / "SPY.parquet")
            source = root / "atvi.parquet"
            _prices(dates.delete(2)).to_parquet(source)
            with self.assertRaisesRegex(ValueError, "omits"):
                validate_historical_import(
                    source, "ATVI", candidate, "Source",
                    "https://example.com/data", "raw", "2023-01-02", "2023-01-10",
                )

    def test_duplicate_dates_and_bad_ohlc_are_rejected(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            duplicate = _prices(pd.to_datetime(["2023-01-03", "2023-01-03"]))
            path = root / "duplicate.parquet"
            duplicate.to_parquet(path)
            with self.assertRaisesRegex(ValueError, "duplicate"):
                load_price_file(path)
            bad = _prices(pd.to_datetime(["2023-01-03"]))
            bad.loc[:, "High"] = 8.0
            with self.assertRaisesRegex(ValueError, "high/low"):
                from benchmarking.recover_data import validate_refresh
                validate_refresh(None, bad, bad.index)

    def test_production_directories_are_refused(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            frame = _prices(pd.to_datetime(["2023-01-03"]))
            report = {"ticker": "ATVI"}
            with self.assertRaisesRegex(ValueError, "production/runtime"):
                stage_historical_import(frame, report, root / "data_cache" / "staged", project_root=root)

    def test_source_url_cannot_record_query_credentials(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate = root / "candidate"
            candidate.mkdir()
            dates = pd.bdate_range("2023-01-02", "2023-01-03")
            _prices(dates).to_parquet(candidate / "SPY.parquet")
            source = root / "atvi.parquet"
            _prices(dates).to_parquet(source)
            with self.assertRaisesRegex(ValueError, "query"):
                validate_historical_import(
                    source, "ATVI", candidate, "Source",
                    "https://example.com/data?apikey=secret", "raw",
                    "2023-01-02", "2023-01-03",
                )


if __name__ == "__main__":
    unittest.main()
