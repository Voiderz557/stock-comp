"""Cached, provider-aware historical market-data loading."""

import json
import logging
import queue
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf

from config import (
    CACHE_COVERAGE_TOLERANCE_DAYS,
    MANUAL_HISTORICAL_DATA_DIR,
    MARKET_DATA_CACHE_DIR,
    YFINANCE_HARD_TIMEOUT_SECONDS,
    YFINANCE_REQUEST_TIMEOUT_SECONDS,
)
from data.ticker_history import (
    MISSING_FROM_PROVIDER,
    NOT_PUBLIC_YET,
    get_provider_symbol,
    get_ticker_identity,
    lifecycle_status_for_period,
)


LOGGER = logging.getLogger(__name__)
PRICE_COLUMNS = ["Open", "High", "Low", "Close", "Volume"]
MANIFEST_NAME = "coverage.json"
FAILURE_CACHE_NAME = "data_failures.json"


def _empty_price_frame():
    return pd.DataFrame(columns=PRICE_COLUMNS, index=pd.DatetimeIndex([]))


def _resolve_project_path(path):
    path = Path(path)
    if not path.is_absolute():
        path = Path(__file__).resolve().parents[1] / path
    return path.resolve()


def _resolve_cache_dir(cache_dir=None):
    return _resolve_project_path(cache_dir or MARKET_DATA_CACHE_DIR)


def _cache_path(cache_dir, ticker):
    safe_ticker = ticker.replace("/", "-").replace("\\", "-")
    return Path(cache_dir) / f"{safe_ticker}.parquet"


def _merge_ranges(ranges):
    merged = []
    for start, end_exclusive in sorted(ranges, key=lambda item: item[0]):
        if start >= end_exclusive:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end_exclusive))
        else:
            merged.append((start, end_exclusive))
    return merged


def _missing_row_ranges(data, start, end_exclusive, tolerance_days=None):
    """Find boundary truncation and implausibly large holes in cached rows."""
    tolerance_days = (
        CACHE_COVERAGE_TOLERANCE_DAYS
        if tolerance_days is None
        else tolerance_days
    )
    requested = data.loc[(data.index >= start) & (data.index < end_exclusive)]
    if requested.empty:
        return [(start, end_exclusive)]

    dates = requested.index.sort_values().unique()
    missing = []
    first_date = dates[0]
    last_date = dates[-1]
    requested_last = end_exclusive - pd.Timedelta(days=1)
    tolerance = pd.Timedelta(days=tolerance_days)

    if first_date - start > tolerance:
        missing.append((start, first_date))
    if requested_last - last_date > tolerance:
        missing.append((last_date + pd.Timedelta(days=1), end_exclusive))

    for previous, following in zip(dates[:-1], dates[1:]):
        if following - previous > tolerance:
            missing.append((previous + pd.Timedelta(days=1), following))
    return _merge_ranges(missing)


def _required_integrity_ranges(ticker, start, end_exclusive, required_ranges):
    if required_ranges is None:
        return [(start, end_exclusive)]
    ranges = []
    for required_start, required_end in required_ranges.get(ticker, []):
        overlap_start = max(start, pd.Timestamp(required_start).normalize())
        overlap_end = min(
            end_exclusive,
            pd.Timestamp(required_end).normalize() + pd.Timedelta(days=1),
        )
        if overlap_start < overlap_end:
            ranges.append((overlap_start, overlap_end))
    return _merge_ranges(ranges)


def _manifest_coverage_from_rows(coverage, cached):
    """Trim impossible manifest endpoints to the parquet's actual endpoints."""
    if not coverage or cached.empty:
        return None
    try:
        claimed_start = pd.Timestamp(coverage["start"]).normalize()
        claimed_end = pd.Timestamp(coverage["end_exclusive"]).normalize()
    except (KeyError, TypeError, ValueError):
        return None

    actual_start = cached.index.min().normalize()
    actual_end = cached.index.max().normalize() + pd.Timedelta(days=1)
    tolerance = pd.Timedelta(days=CACHE_COVERAGE_TOLERANCE_DAYS)
    repaired_start = (
        claimed_start
        if actual_start - claimed_start <= tolerance
        else actual_start
    )
    repaired_end = (
        claimed_end if claimed_end - actual_end <= tolerance else actual_end
    )
    if repaired_start >= repaired_end:
        return None
    return {
        "start": repaired_start.strftime("%Y-%m-%d"),
        "end_exclusive": repaired_end.strftime("%Y-%m-%d"),
    }


def _cache_fully_covers(
    ticker, path, coverage, start, end_exclusive, required_ranges
):
    if not coverage or not path.exists():
        return False
    try:
        cached = _normalize_download(pd.read_parquet(path), ticker)
        verified = _manifest_coverage_from_rows(coverage, cached)
        if not verified:
            return False
        if pd.Timestamp(verified["start"]) > start:
            return False
        if pd.Timestamp(verified["end_exclusive"]) < end_exclusive:
            return False
        return not any(
            _missing_row_ranges(cached, range_start, range_end)
            for range_start, range_end in _required_integrity_ranges(
                ticker, start, end_exclusive, required_ranges
            )
        )
    except Exception:
        return False


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
    temporary.write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
    )
    temporary.replace(path)


def _read_failure_cache(cache_dir):
    path = Path(cache_dir) / FAILURE_CACHE_NAME
    if not path.exists():
        return []
    try:
        records = json.loads(path.read_text(encoding="utf-8"))
        return records if isinstance(records, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def _write_failure_cache(cache_dir, records):
    path = Path(cache_dir) / FAILURE_CACHE_NAME
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(records, indent=2, sort_keys=True), encoding="utf-8"
    )
    temporary.replace(path)


def _normalize_download(data, provider_symbol=None):
    if data is None or data.empty:
        return _empty_price_frame()
    data = data.copy()
    if isinstance(data.columns, pd.MultiIndex):
        levels = data.columns.get_level_values(-1)
        if provider_symbol in levels:
            data = data.xs(provider_symbol, axis=1, level=-1)
        elif provider_symbol in data.columns.get_level_values(0):
            data = data[provider_symbol]
        elif len(set(levels)) == 1:
            data = data.xs(levels[0], axis=1, level=-1)
    data.index = pd.to_datetime(data.index).tz_localize(None).normalize()
    columns = [column for column in PRICE_COLUMNS if column in data.columns]
    return data[columns].sort_index()


def _download_range(ticker, start, end_exclusive):
    """Primary provider: yfinance, with known historical symbol routing."""
    provider_symbol = get_provider_symbol(ticker)
    downloaded = yf.download(
        tickers=provider_symbol,
        start=start.strftime("%Y-%m-%d"),
        end=end_exclusive.strftime("%Y-%m-%d"),
        auto_adjust=True,
        threads=False,
        progress=False,
        timeout=YFINANCE_REQUEST_TIMEOUT_SECONDS,
    )
    return _normalize_download(downloaded, provider_symbol)


def _download_with_hard_timeout(ticker, start, end_exclusive):
    """Bound the complete yfinance call, including its metadata processing."""
    results = queue.Queue(maxsize=1)

    def worker():
        try:
            results.put((True, _download_range(ticker, start, end_exclusive)))
        except Exception as error:
            results.put((False, error))

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    try:
        succeeded, value = results.get(timeout=YFINANCE_HARD_TIMEOUT_SECONDS)
    except queue.Empty as error:
        raise TimeoutError(
            f"yfinance exceeded {YFINANCE_HARD_TIMEOUT_SECONDS} seconds"
        ) from error
    if not succeeded:
        raise value
    return value


class LocalParquetProvider:
    """Optional secondary provider for manually supplied historical files."""

    name = "LOCAL PARQUET"

    def __init__(self, data_dir=None):
        self.data_dir = _resolve_project_path(
            data_dir or MANUAL_HISTORICAL_DATA_DIR
        )

    def fetch(self, ticker, start, end_exclusive):
        path = _cache_path(self.data_dir, ticker)
        if not path.exists():
            return _empty_price_frame()
        data = _normalize_download(pd.read_parquet(path), ticker)
        return data.loc[(data.index >= start) & (data.index < end_exclusive)]


def _range_overlaps(left, right):
    left_start, left_end_exclusive = left
    right_start, right_end_inclusive = right
    return left_start <= right_end_inclusive and left_end_exclusive > right_start


def _is_required_range(ticker, requested_range, required_ranges):
    if required_ranges is None:
        return True
    return any(
        _range_overlaps(requested_range, valid_range)
        for valid_range in required_ranges.get(ticker, [])
    )


def _failure(ticker, start, end_exclusive, reason, timestamp=None):
    identity = get_ticker_identity(ticker)
    item = {
        "Ticker": ticker,
        "Requested Start": start.strftime("%Y-%m-%d"),
        "Requested End": (end_exclusive - pd.Timedelta(days=1)).strftime(
            "%Y-%m-%d"
        ),
        "Data Status": MISSING_FROM_PROVIDER,
        "Lifecycle Status": identity.lifecycle_status,
        "Provider Symbol": get_provider_symbol(ticker),
        "Provider": "yfinance",
        "Timestamp": timestamp or datetime.now(timezone.utc).isoformat(),
        "Reason": reason,
    }
    LOGGER.warning(
        "DATA SOURCE FAILURE ticker=%s start=%s end=%s reason=%s",
        item["Ticker"],
        item["Requested Start"],
        item["Requested End"],
        item["Reason"],
    )
    return item


def _store_failure(failure_cache, failure):
    key = (
        failure["Ticker"],
        failure["Requested Start"],
        failure["Requested End"],
        failure["Provider"],
    )
    for existing in failure_cache:
        existing_key = (
            existing.get("Ticker"),
            existing.get("Requested Start"),
            existing.get("Requested End"),
            existing.get("Provider"),
        )
        if existing_key == key:
            existing.update(failure)
            return
    failure_cache.append(failure)


def _known_failure_segments(failure_cache, ticker, start, end_exclusive):
    segments = []
    for record in failure_cache:
        if record.get("Ticker") != ticker or record.get("Provider") != "yfinance":
            continue
        record_start = pd.Timestamp(record["Requested Start"])
        record_end = pd.Timestamp(record["Requested End"]) + pd.Timedelta(days=1)
        overlap_start = max(start, record_start)
        overlap_end = min(end_exclusive, record_end)
        if overlap_start < overlap_end:
            segments.append((overlap_start, overlap_end, record))
    segments.sort(key=lambda item: item[0])
    return segments


def _subtract_failed_segments(start, end_exclusive, failed_segments):
    uncovered = []
    cursor = start
    for failed_start, failed_end, _ in failed_segments:
        if failed_end <= cursor:
            continue
        if failed_start > cursor:
            uncovered.append((cursor, min(failed_start, end_exclusive)))
        cursor = max(cursor, failed_end)
        if cursor >= end_exclusive:
            break
    if cursor < end_exclusive:
        uncovered.append((cursor, end_exclusive))
    return [(left, right) for left, right in uncovered if left < right]


def _try_secondary_providers(providers, ticker, start, end_exclusive):
    errors = []
    for provider in providers:
        try:
            fallback = provider.fetch(ticker, start, end_exclusive)
        except Exception as error:
            errors.append(f"{provider.name} error: {error}")
            continue
        if not fallback.empty:
            return fallback, provider.name, errors
    return _empty_price_frame(), None, errors


def _load_ticker(
    ticker,
    start,
    end_exclusive,
    cache_dir,
    manifest,
    required_ranges=None,
    secondary_providers=None,
    failure_cache=None,
):
    path = _cache_path(cache_dir, ticker)
    requested_start = start
    requested_end = end_exclusive
    cache_error = None
    try:
        cached = (
            _normalize_download(pd.read_parquet(path), ticker)
            if path.exists()
            else _empty_price_frame()
        )
    except Exception as error:
        cached = _empty_price_frame()
        cache_error = f"Unreadable cache file: {error}"
    claimed_coverage = (
        manifest.get(ticker)
        if path.exists() and cache_error is None and not cached.empty
        else None
    )
    coverage = _manifest_coverage_from_rows(claimed_coverage, cached)
    if coverage != claimed_coverage:
        if coverage is None:
            manifest.pop(ticker, None)
        else:
            manifest[ticker] = coverage
    if coverage:
        cached_request = cached.loc[
            (cached.index >= requested_start) & (cached.index < requested_end)
        ]
        if cached_request.empty and _is_required_range(
            ticker, (requested_start, requested_end), required_ranges
        ):
            coverage = None
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

    integrity_ranges = _required_integrity_ranges(
        ticker, requested_start, requested_end, required_ranges
    )
    for integrity_start, integrity_end in integrity_ranges:
        missing_ranges.extend(
            _missing_row_ranges(cached, integrity_start, integrity_end)
        )
    missing_ranges = _merge_ranges(missing_ranges)

    frames = [cached] if not cached.empty else []
    failures = []
    secondary_sources = []
    all_fetches_succeeded = True
    providers = (
        (LocalParquetProvider(),)
        if secondary_providers is None
        else tuple(secondary_providers)
    )
    failure_cache = failure_cache if failure_cache is not None else []
    attempted_yfinance = False

    for missing_start, missing_end in missing_ranges:
        if missing_start >= missing_end:
            continue
        known_segments = _known_failure_segments(
            failure_cache, ticker, missing_start, missing_end
        )
        for failed_start, failed_end, record in known_segments:
            fallback, source, fallback_errors = _try_secondary_providers(
                providers, ticker, failed_start, failed_end
            )
            if not fallback.empty:
                frames.append(fallback)
                secondary_sources.append({"Ticker": ticker, "Source": source})
                continue
            all_fetches_succeeded = False
            if _is_required_range(
                ticker, (failed_start, failed_end), required_ranges
            ):
                cached_failure = dict(record)
                cached_failure["Cached Failure"] = True
                if fallback_errors:
                    cached_failure["Reason"] = (
                        f"{cached_failure.get('Reason', '')}; "
                        + "; ".join(fallback_errors)
                    )
                failures.append(cached_failure)

        uncovered_ranges = _subtract_failed_segments(
            missing_start, missing_end, known_segments
        )
        for fetch_start, fetch_end in uncovered_ranges:
            primary_reason = cache_error
            attempted_yfinance = True
            try:
                fetched = _download_with_hard_timeout(
                    ticker, fetch_start, fetch_end
                )
                if fetched.empty:
                    primary_reason = "yfinance returned no rows"
            except Exception as error:
                fetched = _empty_price_frame()
                primary_reason = f"yfinance error: {error}"

            if fetched.empty:
                fallback, source, fallback_errors = _try_secondary_providers(
                    providers, ticker, fetch_start, fetch_end
                )
                if fallback_errors:
                    primary_reason = (
                        f"{primary_reason}; " + "; ".join(fallback_errors)
                    )
                if not fallback.empty:
                    fetched = fallback
                    secondary_sources.append({"Ticker": ticker, "Source": source})

            if fetched.empty:
                all_fetches_succeeded = False
                lifecycle = lifecycle_status_for_period(
                    ticker, fetch_start, fetch_end - pd.Timedelta(days=1)
                )
                if lifecycle != NOT_PUBLIC_YET and _is_required_range(
                    ticker, (fetch_start, fetch_end), required_ranges
                ):
                    failure = _failure(
                        ticker,
                        fetch_start,
                        fetch_end,
                        primary_reason or "No provider returned historical rows",
                    )
                    failures.append(failure)
                    _store_failure(failure_cache, failure)
            else:
                frames.append(fetched)
                if _missing_row_ranges(fetched, fetch_start, fetch_end):
                    all_fetches_succeeded = False

    if frames:
        combined = pd.concat(frames).sort_index()
        combined = combined[~combined.index.duplicated(keep="last")]
        Path(cache_dir).mkdir(parents=True, exist_ok=True)
        combined.to_parquet(path)
    else:
        combined = _empty_price_frame()

    downloaded = attempted_yfinance
    remaining_integrity_gaps = [
        gap
        for integrity_start, integrity_end in integrity_ranges
        for gap in _missing_row_ranges(combined, integrity_start, integrity_end)
    ]
    if downloaded and all_fetches_succeeded and not remaining_integrity_gaps:
        if coverage:
            start = min(start, pd.Timestamp(coverage["start"]))
            end_exclusive = max(
                end_exclusive, pd.Timestamp(coverage["end_exclusive"])
            )
        manifest[ticker] = {
            "start": start.strftime("%Y-%m-%d"),
            "end_exclusive": end_exclusive.strftime("%Y-%m-%d"),
        }
    elif remaining_integrity_gaps:
        # Keep only endpoint coverage supported by actual rows. Internal holes
        # are independently rediscovered from the parquet on every cache hit.
        candidate_coverage = coverage or {
            "start": requested_start.strftime("%Y-%m-%d"),
            "end_exclusive": requested_end.strftime("%Y-%m-%d"),
        }
        repaired_coverage = _manifest_coverage_from_rows(
            candidate_coverage, combined
        )
        if repaired_coverage:
            manifest[ticker] = repaired_coverage
        else:
            manifest.pop(ticker, None)

    requested = combined.loc[
        (combined.index >= requested_start) & (combined.index < requested_end)
    ]
    return requested, downloaded, failures, secondary_sources


def _validate_required_coverage(data, required_ranges, existing_failures):
    if not required_ranges:
        return existing_failures
    failures = list(existing_failures)
    failed_keys = {
        (item["Ticker"], item["Requested Start"], item["Requested End"])
        for item in failures
    }
    failed_tickers = {item["Ticker"] for item in failures}
    for ticker, ranges in required_ranges.items():
        if ticker in failed_tickers:
            continue
        frame = data.get(ticker, _empty_price_frame())
        for start, end in ranges:
            if not frame.empty:
                available = frame.loc[(frame.index >= start) & (frame.index <= end)]
                if not available.empty:
                    continue
            lifecycle = lifecycle_status_for_period(ticker, start, end)
            if lifecycle == NOT_PUBLIC_YET:
                continue
            key = (ticker, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
            if key not in failed_keys:
                failures.append(
                    _failure(
                        ticker,
                        start,
                        end + pd.Timedelta(days=1),
                        "No cached or provider rows overlap valid membership period",
                    )
                )
                failed_keys.add(key)
    return failures


def load_market_data(
    tickers,
    start_date,
    end_date,
    status_callback=None,
    cache_dir=None,
    required_ranges=None,
    secondary_providers=None,
):
    """Load prices without conflating provider failures with membership."""
    cache_dir = _resolve_cache_dir(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    manifest = _read_manifest(cache_dir)
    failure_cache = _read_failure_cache(cache_dir)
    start = pd.Timestamp(start_date).normalize()
    end_exclusive = pd.Timestamp(end_date).normalize() + pd.Timedelta(days=1)
    data = {}
    downloaded_tickers = []
    failures = []
    secondary_sources = []

    for ticker in dict.fromkeys(tickers):
        coverage = manifest.get(ticker)
        fully_cached = _cache_fully_covers(
            ticker,
            _cache_path(cache_dir, ticker),
            coverage,
            start,
            end_exclusive,
            required_ranges,
        )
        if status_callback:
            status_callback("USING CACHE" if fully_cached else "DOWNLOADING", ticker)
        frame, downloaded, ticker_failures, ticker_secondary = _load_ticker(
            ticker,
            start,
            end_exclusive,
            cache_dir,
            manifest,
            required_ranges=required_ranges,
            secondary_providers=secondary_providers,
            failure_cache=failure_cache,
        )
        data[ticker] = frame
        failures.extend(ticker_failures)
        secondary_sources.extend(ticker_secondary)
        if downloaded:
            downloaded_tickers.append(ticker)

    failures = _validate_required_coverage(data, required_ranges, failures)
    _write_manifest(cache_dir, manifest)
    _write_failure_cache(cache_dir, failure_cache)
    unavailable = sorted({item["Ticker"] for item in failures})
    classifications = {
        ticker: {
            "Lifecycle Status": get_ticker_identity(ticker).lifecycle_status,
            "Period Status": lifecycle_status_for_period(
                ticker, start, end_exclusive - pd.Timedelta(days=1)
            ),
            "Provider Symbol": get_provider_symbol(ticker),
            "Public Start": (
                get_ticker_identity(ticker).public_start.strftime("%Y-%m-%d")
                if get_ticker_identity(ticker).public_start is not None
                else None
            ),
        }
        for ticker in sorted(set(tickers))
    }
    return data, {
        "Status": "DOWNLOADING" if downloaded_tickers else "USING CACHE",
        "Downloaded Tickers": downloaded_tickers,
        "Cache Directory": str(cache_dir),
        "Data Source Failures": failures,
        "Unavailable Valid Constituents": unavailable,
        "Secondary Sources Used": secondary_sources,
        "Ticker Classifications": classifications,
    }


def clear_market_data_cache(cache_dir=None):
    """Delete only the configured cache directory and its contents."""
    cache_dir = _resolve_cache_dir(cache_dir)
    project_dir = Path(__file__).resolve().parents[1]
    if cache_dir == project_dir or project_dir not in cache_dir.parents:
        raise ValueError("Cache directory must be a child of the project directory.")
    if cache_dir.exists():
        shutil.rmtree(cache_dir)
