"""Validate and stage user-supplied historical OHLCV without promoting it.

The importer is deliberately source-neutral. It writes only to an explicit staging
directory and refuses the project's production/runtime price directories. A staged
file still requires human provenance review before it can join a benchmark cache.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pandas as pd

from benchmarking.recover_data import validate_refresh
from data.ticker_history import listed_price_range


REQUIRED_COLUMNS = ("Open", "High", "Low", "Close", "Volume")
DEFAULT_REQUIRED_START = "2020-04-29"
DEFAULT_REQUIRED_END = "2025-12-31"


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def sanitize_source_url(value):
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Source URL must be an http(s) provenance URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Source URL must not contain credentials, query parameters, or fragments")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def load_price_file(path):
    """Load CSV/Parquet and normalize a date index without repairing bad rows."""
    path = Path(path)
    if path.suffix.lower() == ".csv":
        frame = pd.read_csv(path)
    elif path.suffix.lower() in {".parquet", ".pq"}:
        frame = pd.read_parquet(path)
    else:
        raise ValueError("Historical import must be CSV or Parquet")
    if not isinstance(frame.index, pd.DatetimeIndex):
        date_column = next((column for column in frame.columns if str(column).lower() == "date"), None)
        if date_column is None:
            raise ValueError("Input requires a DatetimeIndex or Date column")
        dates = pd.to_datetime(frame.pop(date_column), errors="coerce")
        if dates.isna().any():
            raise ValueError("Input contains invalid dates")
        frame.index = pd.DatetimeIndex(dates)
    if frame.index.tz is not None:
        frame.index = frame.index.tz_convert(None)
    frame.index = frame.index.normalize()
    if frame.index.has_duplicates:
        raise ValueError("Input contains duplicate trading dates")
    by_lower = {str(column).lower(): column for column in frame.columns}
    missing = [column for column in REQUIRED_COLUMNS if column.lower() not in by_lower]
    if missing:
        raise ValueError(f"Input is missing required columns: {', '.join(missing)}")
    frame = frame.rename(columns={by_lower[column.lower()]: column for column in REQUIRED_COLUMNS})
    return frame.loc[:, list(REQUIRED_COLUMNS)].sort_index()


def required_sessions(reference, ticker, required_start, required_end):
    if reference is None or reference.empty or not isinstance(reference.index, pd.DatetimeIndex):
        raise ValueError("A dated SPY reference series is required")
    start = pd.Timestamp(required_start).normalize()
    end = pd.Timestamp(required_end).normalize()
    if start > end:
        raise ValueError("Required start must not be after required end")
    listed = listed_price_range(ticker, start, end + pd.Timedelta(days=1))
    if listed is None:
        raise ValueError(f"{ticker} has no known listed sessions in the requested range")
    listed_start, listed_end = listed
    return reference.index[(reference.index >= listed_start) & (reference.index < listed_end)]


def validate_historical_import(
    input_path,
    ticker,
    candidate_dir,
    source_name,
    source_url,
    adjustment_convention,
    required_start=DEFAULT_REQUIRED_START,
    required_end=DEFAULT_REQUIRED_END,
):
    ticker = str(ticker).strip().upper()
    if not ticker or not ticker.replace("-", "").isalnum():
        raise ValueError("Ticker contains unsupported characters")
    if adjustment_convention not in {"adjusted", "raw"}:
        raise ValueError("Adjustment convention must be explicitly declared as adjusted or raw")
    if not str(source_name).strip():
        raise ValueError("Source name is required")
    source_url = sanitize_source_url(source_url)
    candidate_dir = Path(candidate_dir).resolve()
    reference_path = candidate_dir / "SPY.parquet"
    if not reference_path.is_file():
        raise ValueError("Candidate directory must contain SPY.parquet")
    reference = pd.read_parquet(reference_path)
    expected = required_sessions(reference, ticker, required_start, required_end)
    if expected.empty:
        raise ValueError("No required sessions were resolved")
    frame = load_price_file(input_path)
    existing_path = candidate_dir / f"{ticker}.parquet"
    existing = pd.read_parquet(existing_path) if existing_path.is_file() else None
    validation = validate_refresh(existing, frame, expected)
    return frame, {
        "ticker": ticker,
        "source_name": str(source_name).strip(),
        "source_url": source_url,
        "adjustment_convention_declared": adjustment_convention,
        "adjustment_convention_independently_verified": False,
        "input_file": Path(input_path).name,
        "input_sha256": sha256_file(input_path),
        "required_start": str(pd.Timestamp(required_start).date()),
        "required_end": str(pd.Timestamp(required_end).date()),
        "required_sessions": len(expected),
        "validated_at_utc": datetime.now(timezone.utc).isoformat(),
        "validation": validation,
        "status": "VALIDATED_FOR_STAGING_NOT_PROMOTED",
    }


def _is_within(path, parent):
    try:
        Path(path).resolve().relative_to(Path(parent).resolve())
        return True
    except ValueError:
        return False


def stage_historical_import(frame, report, stage_dir, project_root=None):
    """Write a validated import outside protected production/runtime directories."""
    project_root = Path(project_root or Path(__file__).resolve().parents[1]).resolve()
    stage_dir = Path(stage_dir).resolve()
    protected = [project_root / "data_cache", project_root / "historical_data"]
    if any(_is_within(stage_dir, path) for path in protected):
        raise ValueError("Refusing to stage inside a production/runtime price directory")
    stage_dir.mkdir(parents=True, exist_ok=True)
    ticker = report["ticker"]
    price_path = stage_dir / f"{ticker}.parquet"
    manifest_path = stage_dir / f"{ticker}.manifest.json"
    if price_path.exists() or manifest_path.exists():
        raise FileExistsError(f"Staged import already exists for {ticker}")
    frame.to_parquet(price_path)
    staged_report = dict(report)
    staged_report["staged_parquet_sha256"] = sha256_file(price_path)
    manifest_path.write_text(json.dumps(staged_report, indent=2) + "\n", encoding="utf-8")
    return price_path, manifest_path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--candidate-dir", required=True, type=Path)
    parser.add_argument("--stage-dir", required=True, type=Path)
    parser.add_argument("--source-name", required=True)
    parser.add_argument("--source-url", required=True)
    parser.add_argument("--adjustment-convention", choices=("adjusted", "raw"), required=True)
    parser.add_argument("--required-start", default=DEFAULT_REQUIRED_START)
    parser.add_argument("--required-end", default=DEFAULT_REQUIRED_END)
    args = parser.parse_args(argv)
    frame, report = validate_historical_import(
        args.input, args.ticker, args.candidate_dir, args.source_name,
        args.source_url, args.adjustment_convention, args.required_start,
        args.required_end,
    )
    price_path, manifest_path = stage_historical_import(frame, report, args.stage_dir)
    print(json.dumps({"price_file": str(price_path), "manifest": str(manifest_path), **report}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
