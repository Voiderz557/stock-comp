"""Cached historical market-data loading, independent of signal generation."""

import json
import shutil
from pathlib import Path

import pandas as pd
import yfinance as yf

from config import MARKET_DATA_CACHE_DIR


PRICE_COLUMNS = ["Open", "High", "Low", "Close", "Volume"]
MANIFEST_NAME = "coverage.json"


def _resolve_cache_dir(cache_dir=None):
    cache_dir = Path(cache_dir or MARKET_DATA_CACHE_DIR)
    if not cache_dir.is_absolute():
        cache_dir = Path(__file__).resolve().parent / cache_dir
    return cache_dir.resolve()


def _cache_path(cache_dir, ticker):
    safe_ticker = ticker.replace("/", "-").replace("\\", "-")
    return Path(cache_dir) / f"{safe_ticker}.parquet"


def _read_manifest(cache_dir):
    path = Path(cache_dir) / MANIFEST_NAME
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _write_manifest(cache_dir, manifest):
    path = Path(cache_dir) / MANIFEST_NAME
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def _normalize_download(data, ticker):
    if data is None or data.empty:
        return pd.DataFrame(columns=PRICE_COLUMNS)
    data = data.copy()
    if isinstance(data.columns, pd.MultiIndex):
        if ticker in data.columns.get_level_values(0):
            data = data[ticker]
        elif ticker in data.columns.get_level_values(-1):
            data = data.xs(ticker, axis=1, level=-1)
    data.index = pd.to_datetime(data.index).tz_localize(None).normalize()
    columns = [column for column in PRICE_COLUMNS if column in data.columns]
    return data[columns].sort_index()


def _download_range(ticker, start, end_exclusive):
    return _normalize_download(
        yf.download(
            tickers=ticker,
            start=start.strftime("%Y-%m-%d"),
            end=end_exclusive.strftime("%Y-%m-%d"),
            auto_adjust=True,
            threads=False,
            progress=False,
        ),
        ticker,
    )


def _load_ticker(ticker, start, end_exclusive, cache_dir, manifest):
    path = _cache_path(cache_dir, ticker)
    cached = pd.read_parquet(path) if path.exists() else pd.DataFrame()
    coverage = manifest.get(ticker) if path.exists() else None
    requested_start = start
    requested_end = end_exclusive
    missing_ranges = []

    if coverage:
        covered_start = pd.Timestamp(coverage["start"])
        covered_end = pd.Timestamp(coverage["end_exclusive"])
        if start < covered_start:
            missing_ranges.append((start, min(end_exclusive, covered_start)))
        if end_exclusive > covered_end:
            missing_ranges.append((max(start, covered_end), end_exclusive))
    else:
        missing_ranges.append((start, end_exclusive))

    frames = [cached] if not cached.empty else []
    for missing_start, missing_end in missing_ranges:
        if missing_start < missing_end:
            frames.append(_download_range(ticker, missing_start, missing_end))

    if frames:
        combined = pd.concat(frames).sort_index()
        combined = combined[~combined.index.duplicated(keep="last")]
    else:
        combined = pd.DataFrame(columns=PRICE_COLUMNS)

    downloaded = bool(missing_ranges)
    if downloaded:
        Path(cache_dir).mkdir(parents=True, exist_ok=True)
        combined.to_parquet(path)
        if coverage:
            start = min(start, pd.Timestamp(coverage["start"]))
            end_exclusive = max(
                end_exclusive, pd.Timestamp(coverage["end_exclusive"])
            )
        manifest[ticker] = {
            "start": start.strftime("%Y-%m-%d"),
            "end_exclusive": end_exclusive.strftime("%Y-%m-%d"),
        }

    requested = combined.loc[
        (combined.index >= requested_start) & (combined.index < requested_end)
    ]
    return requested, downloaded


def load_market_data(tickers, start_date, end_date, status_callback=None, cache_dir=None):
    """Load ticker frames, fetching only ranges not covered by the cache."""
    cache_dir = _resolve_cache_dir(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    manifest = _read_manifest(cache_dir)
    start = pd.Timestamp(start_date).normalize()
    end_exclusive = pd.Timestamp(end_date).normalize() + pd.Timedelta(days=1)
    data = {}
    downloaded_tickers = []

    for ticker in sorted(set(tickers)):
        coverage = manifest.get(ticker)
        fully_cached = (
            coverage
            and pd.Timestamp(coverage["start"]) <= start
            and pd.Timestamp(coverage["end_exclusive"]) >= end_exclusive
            and _cache_path(cache_dir, ticker).exists()
        )
        if status_callback:
            status_callback("USING CACHE" if fully_cached else "DOWNLOADING", ticker)
        frame, downloaded = _load_ticker(
            ticker, start, end_exclusive, cache_dir, manifest
        )
        data[ticker] = frame
        if downloaded:
            downloaded_tickers.append(ticker)

    _write_manifest(cache_dir, manifest)
    return data, {
        "Status": "DOWNLOADING" if downloaded_tickers else "USING CACHE",
        "Downloaded Tickers": downloaded_tickers,
        "Cache Directory": str(cache_dir.resolve()),
    }


def clear_market_data_cache(cache_dir=None):
    """Delete only the configured cache directory and its contents."""
    cache_dir = _resolve_cache_dir(cache_dir)
    project_dir = Path(__file__).resolve().parent
    if cache_dir == project_dir or project_dir not in cache_dir.parents:
        raise ValueError("Cache directory must be a child of the project directory.")
    if cache_dir.exists():
        shutil.rmtree(cache_dir)
