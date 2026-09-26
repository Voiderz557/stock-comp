"""Non-destructive recovery of historical price inputs into a separate directory.

Original files are hashed and backed up; refreshed series replace whole candidate
files, never individual adjusted-price rows. Existing provider failures are honored
unless an explicitly documented symbol-history discovery justifies a new probe.
This utility neither promotes the cache nor removes historical constituents.
"""
from datetime import datetime, timezone
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import time

import numpy as np
import pandas as pd

from benchmarking.coverage import audit_price_coverage
from data.historical_universe import get_membership_ranges
from data.market_data import _download_with_hard_timeout, _known_failure_segments


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def relevant_failure_segments(failure_cache, ticker, start, end_exclusive, required_dates):
    """Return cached failures that overlap an actually required trading session.

    Provider failures are range-specific.  In particular, a failed weekend probe
    must not suppress a later request for a multi-year trading history.
    """
    required = pd.DatetimeIndex(required_dates)
    relevant = []
    for failed_start, failed_end, record in _known_failure_segments(
        failure_cache, ticker, start, end_exclusive
    ):
        if ((required >= failed_start) & (required < failed_end)).any():
            relevant.append((failed_start, failed_end, record))
    return relevant


def refresh_bounds(old, required_dates):
    """Cover warmup, required observations, and every existing cached row."""
    required = pd.DatetimeIndex(required_dates)
    warmup_start = pd.Timestamp("2020-04-29")
    if old is None or old.empty:
        return warmup_start, required.max() + pd.Timedelta(days=1)
    first = min(old.index.min(), warmup_start if old.index.min() > required.min() else old.index.min())
    end = max(old.index.max(), required.max()) + pd.Timedelta(days=1)
    return first, end


def validate_refresh(old, fresh, required_dates):
    """Reject partial/malformed replacements; return overlap adjustment diagnostics."""
    if fresh is None or fresh.empty or not isinstance(fresh.index, pd.DatetimeIndex):
        raise ValueError("No dated provider rows")
    if fresh.index.has_duplicates or not fresh.index.is_monotonic_increasing:
        raise ValueError("Provider dates are duplicated or unsorted")
    required_columns = ["Open", "High", "Low", "Close", "Volume"]
    if not set(required_columns).issubset(fresh.columns):
        raise ValueError("Incomplete OHLCV schema")
    numeric = fresh[required_columns].apply(pd.to_numeric, errors="coerce")
    if not np.isfinite(numeric.to_numpy()).all():
        raise ValueError("Nonfinite OHLCV")
    if (numeric[["Open", "High", "Low", "Close"]] <= 0).any().any() or (numeric.Volume < 0).any():
        raise ValueError("Invalid price/volume")
    if ((numeric.High < numeric[["Open", "Close", "Low"]].max(axis=1)) |
            (numeric.Low > numeric[["Open", "Close", "High"]].min(axis=1))).any():
        raise ValueError("Inconsistent high/low bounds")
    expected = pd.DatetimeIndex(required_dates).union(old.index if old is not None and not old.empty else [])
    missing = expected.difference(fresh.index)
    if len(missing):
        raise ValueError(f"Fresh series omits {len(missing)} required/existing rows; first {missing[0]}")
    detail = {"rows": len(fresh), "first": str(fresh.index.min().date()), "last": str(fresh.index.max().date())}
    if old is not None and not old.empty:
        overlap = old.index.intersection(fresh.index)
        ratios = fresh.loc[overlap, "Close"] / old.loc[overlap, "Close"]
        detail.update({"overlap_rows": len(overlap), "close_ratio_min": float(ratios.min()),
                       "close_ratio_max": float(ratios.max()), "close_ratio_median": float(ratios.median())})
    return detail


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--resume",
        type=Path,
        help="Resume one existing isolated recovery directory without re-fetching recovered symbols.",
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    source = root / "data_cache"
    if args.resume:
        output = args.resume.resolve()
        expected_parent = (root / "benchmark_exports").resolve()
        if output.parent != expected_parent or not output.name.startswith("recovery-"):
            raise ValueError("--resume must name a recovery directory under benchmark_exports")
    else:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        output = root / "benchmark_exports" / f"recovery-{stamp}"
    backup, candidate, raw = (output / name for name in ("backup", "candidate", "downloads"))
    if not args.resume:
        for folder in (backup, candidate, raw):
            folder.mkdir(parents=True, exist_ok=False)
    files = sorted(source.glob("*.parquet")) + sorted(source.glob("*.json"))
    prior_report = None
    if args.resume:
        prior_report = json.loads((output / "recovery.json").read_text(encoding="utf-8"))
    hashes = dict(prior_report.get("original_sha256", {})) if prior_report else {}
    prices = {}
    for path in files:
        if not args.resume:
            hashes[path.name] = digest(path)
            shutil.copy2(path, backup / path.name)
            shutil.copy2(path, candidate / path.name)
        if digest(backup / path.name) != hashes[path.name]:
            raise RuntimeError(f"Backup checksum mismatch: {path.name}")
    for candidate_path in sorted(candidate.glob("*.parquet")):
        frame = pd.read_parquet(candidate_path)
        if not frame.empty and isinstance(frame.index, pd.DatetimeIndex):
            prices[candidate_path.stem] = frame
    failures_path = source / "data_failures.json"
    failure_cache = json.loads(failures_path.read_text()) if failures_path.exists() else []
    before = audit_price_coverage(prices, "2023-01-01", "2025-12-31")
    ranges = get_membership_ranges("2023-01-01", "2025-12-31")
    calendar = prices["SPY"].index
    targets = (prior_report.get("after", before)["Unavailable Valid Constituents"]
               if prior_report else before["Unavailable Valid Constituents"])
    report = prior_report or {
        "original_sha256": hashes, "before": before, "requests": [],
        "adjustment_policy": "yfinance auto_adjust=True; full-series refresh, no row splicing",
        "fiserv_evidence": "https://www.nasdaqtrader.com/TraderNews.aspx?id=DTN2025-32",
    }
    def checkpoint():
        (output / "recovery.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    checkpoint()
    print(f"RECOVERY {output}", flush=True)
    started = time.monotonic()
    for index, ticker in enumerate(targets, 1):
        old = prices.get(ticker)
        ticker_ranges = ranges[ticker]
        expected = pd.DatetimeIndex([])
        for first, last in ticker_ranges:
            expected = expected.union(calendar[(calendar >= first) & (calendar <= last)])
        first, end = refresh_bounds(old, expected)
        entry = {"ticker": ticker, "start": str(first.date()), "end_exclusive": str(end.date())}
        report["requests"].append(entry)
        # FI->FISV restoration is new official evidence. Fetch only the historical
        # membership interval here (before the old routing code's June 2023 split).
        if ticker == "FISV":
            end = min(end, pd.Timestamp("2023-06-07"))
            entry["end_exclusive"] = str(end.date())
            entry["failure_cache_override_reason"] = "Official 2025 FISV relisting; successful historical probe"
        elif relevant_failure_segments(failure_cache, ticker, first, end, expected):
            entry["status"] = "SKIPPED_KNOWN_FAILED_RANGE"
            checkpoint()
            print(f"{index}/{len(targets)} {ticker}: known provider failure; no redundant request", flush=True)
            continue
        try:
            downloaded = _download_with_hard_timeout(ticker, first, end)
            downloaded.to_parquet(raw / f"{ticker}.parquet")
            entry.update(validate_refresh(old, downloaded, expected))
            downloaded.to_parquet(candidate / f"{ticker}.parquet")
            prices[ticker] = downloaded
            entry["status"] = "RECOVERED_CANDIDATE_ONLY"
        except Exception as error:
            entry.update({"status": "UNRESOLVED", "error": str(error)})
        checkpoint()
        print(f"{index}/{len(targets)} {ticker}: {entry['status']} ({time.monotonic()-started:.1f}s)", flush=True)
    report["after"] = audit_price_coverage(prices, "2023-01-01", "2025-12-31")
    report["original_unchanged"] = all(digest(source / name) == value for name, value in hashes.items())
    report["elapsed_seconds"] = time.monotonic() - started
    # Candidate coverage is derived from actual rows, rather than copied claims.
    manifest = {ticker: {"start": str(frame.index.min().date()),
                         "end_exclusive": str((frame.index.max() + pd.Timedelta(days=1)).date())}
                for ticker, frame in prices.items()}
    (candidate / "coverage.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    checkpoint()
    print(f"DONE unchanged={report['original_unchanged']} remaining={report['after']['Unavailable Valid Constituents']}", flush=True)
    return 0 if report["after"]["Coverage Is Valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
