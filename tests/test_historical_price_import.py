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


def _write_csv(frame, path):
    frame.rename_axis("Date").reset_index().to_csv(path, index=False)


class HistoricalPriceImportTests(unittest.TestCase):
    def test_valid_file_is_staged_with_provenance_and_checksums(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate = root / "candidate"
            candidate.mkdir()
            dates = pd.bdate_range("2023-01-02", "2023-01-10")
            _write_csv(_prices(dates), candidate / "SPY.csv")
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
            self.assertEqual(len(manifest["staged_price_sha256"]), 64)
            self.assertEqual(manifest["staged_price_file"], price_path.name)
            self.assertFalse(manifest["adjustment_convention_independently_verified"])

    def test_missing_required_session_is_rejected(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate = root / "candidate"
            candidate.mkdir()
            dates = pd.bdate_range("2023-01-02", "2023-01-10")
            _write_csv(_prices(dates), candidate / "SPY.csv")
            source = root / "atvi.csv"
            _write_csv(_prices(dates.delete(2)), source)
            with self.assertRaisesRegex(ValueError, "omits"):
                validate_historical_import(
                    source, "ATVI", candidate, "Source",
                    "https://example.com/data", "raw", "2023-01-02", "2023-01-10",
                )

    def test_duplicate_dates_and_bad_ohlc_are_rejected(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            duplicate = _prices(pd.to_datetime(["2023-01-03", "2023-01-03"]))
            path = root / "duplicate.csv"
            _write_csv(duplicate, path)
            with self.assertRaisesRegex(ValueError, "duplicate"):
                load_price_file(path)
            bad = _prices(pd.to_datetime(["2023-01-03"]))
            bad.loc[:, "High"] = 8.0
            with self.assertRaisesRegex(ValueError, "high/low"):
                from benchmarking.recover_data import validate_refresh
                validate_refresh(None, bad, bad.index)

    def test_massive_compact_aggregate_columns_are_normalized(self):
        with TemporaryDirectory() as temporary:
            source = Path(temporary) / "massive.csv"
            pd.DataFrame(
                {
                    "ticker": ["EA", "EA"],
                    "date": ["2025-09-02", "2025-09-03"],
                    "o": [150.0, 151.0],
                    "h": [152.0, 153.0],
                    "l": [149.0, 150.0],
                    "c": [151.5, 152.5],
                    "v": [1_000_000.0, 1_100_000.0],
                    "vw": [151.1, 152.1],
                    "t": [1756771200000, 1756857600000],
                    "n": [1000, 1100],
                }
            ).to_csv(source, index=False)

            loaded = load_price_file(source)

            self.assertEqual(list(loaded.columns), ["Open", "High", "Low", "Close", "Volume"])
            self.assertEqual(list(loaded.index), list(pd.to_datetime(["2025-09-02", "2025-09-03"])))
            self.assertEqual(float(loaded.iloc[0]["Close"]), 151.5)

    def test_ambiguous_long_and_compact_columns_are_rejected(self):
        with TemporaryDirectory() as temporary:
            source = Path(temporary) / "ambiguous.csv"
            frame = _prices(pd.to_datetime(["2025-09-02"])).rename_axis("Date").reset_index()
            frame["o"] = frame["Open"]
            frame.to_csv(source, index=False)
            with self.assertRaisesRegex(ValueError, "ambiguous price columns"):
                load_price_file(source)

    def test_split_adjusted_convention_is_preserved_in_manifest(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate = root / "candidate"
            candidate.mkdir()
            dates = pd.bdate_range("2023-01-02", "2023-01-10")
            _write_csv(_prices(dates), candidate / "SPY.csv")
            source = root / "ea.csv"
            frame = _prices(dates).rename_axis("Date").reset_index()
            frame.to_csv(source, index=False)

            _, report = validate_historical_import(
                source, "EA", candidate, "Massive daily aggregates",
                "https://massive.com/docs/rest/stocks/aggregates/custom-bars",
                "split_adjusted", "2023-01-02", "2023-01-10",
            )

            self.assertEqual(report["adjustment_convention_declared"], "split_adjusted")

    def test_cached_rows_after_verified_public_end_are_not_required(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate = root / "candidate"
            candidate.mkdir()
            reference_dates = pd.to_datetime(["2026-08-03", "2026-08-04", "2026-08-05"])
            _write_csv(_prices(reference_dates), candidate / "SPY.csv")
            _write_csv(_prices(reference_dates), candidate / "EA.csv")
            source = root / "ea.csv"
            _prices(reference_dates[:2]).rename_axis("Date").reset_index().to_csv(source, index=False)

            _, report = validate_historical_import(
                source, "EA", candidate, "Massive daily aggregates",
                "https://massive.com/docs/rest/stocks/aggregates/custom-bars",
                "split_adjusted", "2026-08-03", "2026-08-04",
            )

            self.assertEqual(report["existing_rows_after_public_end_excluded"], 1)
            self.assertEqual(report["validation"]["last"], "2026-08-04")

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
            _write_csv(_prices(dates), candidate / "SPY.csv")
            source = root / "atvi.csv"
            _write_csv(_prices(dates), source)
            with self.assertRaisesRegex(ValueError, "query"):
                validate_historical_import(
                    source, "ATVI", candidate, "Source",
                    "https://example.com/data?apikey=secret", "raw",
                    "2023-01-02", "2023-01-03",
                )


if __name__ == "__main__":
    unittest.main()
