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
from data.ticker_history import get_ticker_identity, listed_price_range


REQUIRED_COLUMNS = ("Open", "High", "Low", "Close", "Volume")
PRICE_COLUMN_ALIASES = {
    "Open": ("open", "o"),
    "High": ("high", "h"),
    "Low": ("low", "l"),
    "Close": ("close", "c"),
    "Volume": ("volume", "v"),
}
ADJUSTMENT_CONVENTIONS = ("adjusted", "split_adjusted", "total_return_adjusted", "raw")
DEFAULT_REQUIRED_START = "2020-04-29"
DEFAULT_REQUIRED_END = "2025-12-31"
PRICE_FILE_SUFFIXES = (".parquet", ".pq", ".csv")


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
    by_lower = {str(column).strip().lower(): column for column in frame.columns}
    resolved = {}
    ambiguous = {}
    for canonical, aliases in PRICE_COLUMN_ALIASES.items():
        matches = [by_lower[alias] for alias in aliases if alias in by_lower]
        if len(matches) > 1:
            ambiguous[canonical] = [str(match) for match in matches]
        elif matches:
            resolved[canonical] = matches[0]
    if ambiguous:
        details = ", ".join(
            f"{canonical}: {', '.join(columns)}"
            for canonical, columns in sorted(ambiguous.items())
        )
        raise ValueError(f"Input contains ambiguous price columns: {details}")
    missing = [column for column in REQUIRED_COLUMNS if column not in resolved]
    if missing:
        raise ValueError(f"Input is missing required columns: {', '.join(missing)}")
    frame = frame.rename(columns={source: canonical for canonical, source in resolved.items()})
    return frame.loc[:, list(REQUIRED_COLUMNS)].sort_index()


def find_price_file(directory, ticker):
    directory = Path(directory)
    for suffix in PRICE_FILE_SUFFIXES:
        candidate = directory / f"{ticker}{suffix}"
        if candidate.is_file():
            return candidate
    return None


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
    if adjustment_convention not in ADJUSTMENT_CONVENTIONS:
        raise ValueError(
            "Adjustment convention must be explicitly declared as one of: "
            + ", ".join(ADJUSTMENT_CONVENTIONS)
        )
    if not str(source_name).strip():
        raise ValueError("Source name is required")
    source_url = sanitize_source_url(source_url)
    candidate_dir = Path(candidate_dir).resolve()
    reference_path = find_price_file(candidate_dir, "SPY")
    if reference_path is None:
        raise ValueError("Candidate directory must contain a SPY CSV or Parquet price file")
    reference = load_price_file(reference_path)
    expected = required_sessions(reference, ticker, required_start, required_end)
    if expected.empty:
        raise ValueError("No required sessions were resolved")
    frame = load_price_file(input_path)
    existing_path = find_price_file(candidate_dir, ticker)
    existing = load_price_file(existing_path) if existing_path is not None else None
    existing_for_validation = existing
    excluded_post_listing_rows = 0
    public_end = get_ticker_identity(ticker).public_end
    if existing is not None and not existing.empty and public_end is not None:
        public_end = pd.Timestamp(public_end).normalize()
        existing_for_validation = existing.loc[existing.index <= public_end]
        excluded_post_listing_rows = int((existing.index > public_end).sum())
    validation = validate_refresh(existing_for_validation, frame, expected)
    report = {
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
    if excluded_post_listing_rows:
        report["existing_rows_after_public_end_excluded"] = excluded_post_listing_rows
    return frame, report


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
    manifest_path = stage_dir / f"{ticker}.manifest.json"
    existing_price = find_price_file(stage_dir, ticker)
    if existing_price is not None or manifest_path.exists():
        raise FileExistsError(f"Staged import already exists for {ticker}")
    price_path = stage_dir / f"{ticker}.parquet"
    try:
        frame.to_parquet(price_path)
    except (ImportError, ModuleNotFoundError):
        price_path = stage_dir / f"{ticker}.csv"
        frame.rename_axis("Date").to_csv(price_path)
    staged_report = dict(report)
    staged_report["staged_price_file"] = price_path.name
    staged_report["staged_price_sha256"] = sha256_file(price_path)
    if price_path.suffix.lower() in {".parquet", ".pq"}:
        staged_report["staged_parquet_sha256"] = staged_report["staged_price_sha256"]
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
    parser.add_argument(
        "--adjustment-convention", choices=ADJUSTMENT_CONVENTIONS, required=True,
    )
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
