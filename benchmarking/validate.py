"""Offline, instrumented pilot using existing real-price files, never live ledgers.

Run: python -m benchmarking.validate --pilot
Incomplete coverage stops by default. --diagnostic explicitly permits an INVALID
pilot to profile the real runner; its results must not be used to select winners.
No downloads, writes to caches, account initialization, or model tuning occur.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time

import pandas as pd

from benchmarking.coverage import audit_price_coverage
from benchmarking.export import _json_default, build_benchmark_zip
from benchmarking.runner import run_benchmark
from data.market_data import _normalize_download


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pilot", action="store_true")
    parser.add_argument("--diagnostic", action="store_true")
    parser.add_argument(
        "--cache-dir", type=Path,
        help="Read only this price-cache directory (useful for isolated recovery validation).",
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    output = root / "benchmark_exports" / f"validation-{stamp}"
    output.mkdir(parents=True, exist_ok=False)
    prices = {}
    file_errors = []
    folders = (args.cache_dir.resolve(),) if args.cache_dir else (
        root / "data_cache", root / "historical_data"
    )
    for folder in folders:
        for path in sorted(folder.glob("*.parquet")):
            try:
                raw = pd.read_parquet(path)
                if raw.empty:
                    continue
                if not isinstance(raw.index, pd.DatetimeIndex):
                    raise ValueError("non-datetime index")
                frame = _normalize_download(raw, path.stem)
                previous = prices.get(path.stem)
                if previous is not None:
                    frame = pd.concat([previous, frame])
                    frame = frame.loc[~frame.index.duplicated(keep="last")].sort_index()
                prices[path.stem] = frame
            except Exception as error:
                file_errors.append({"Ticker": path.stem, "Error": str(error)})
    coverage = audit_price_coverage(prices, "2023-01-01", "2025-12-31")
    summary = {"scope": (f"isolated cache {folders[0]}; no downloads" if args.cache_dir
                         else "local cached real prices; no downloads"), "coverage": coverage,
               "file_errors": file_errors, "price_frames": len(prices)}
    (output / "coverage.json").write_text(json.dumps(summary, indent=2, default=_json_default), encoding="utf-8")
    print(f"REPORT {output}", flush=True)
    print(f"COVERAGE valid={coverage['Coverage Is Valid']} missing={coverage['Unavailable Valid Constituents']}", flush=True)
    if not args.pilot:
        return 0 if coverage["Coverage Is Valid"] else 2
    if not coverage["Coverage Is Valid"] and not args.diagnostic:
        print("BLOCKED: historical gaps; use --diagnostic only for an explicitly invalid runtime pilot.", flush=True)
        return 2
    last_event = [None, 0.0]
    def progress(event):
        now = time.monotonic()
        key = (event["stage"], event.get("method"))
        with (output / "progress.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, default=_json_default) + "\n")
        if key != last_event[0] or now - last_event[1] >= 15:
            print(f"{event['elapsed_seconds']:.1f}s {key}: {event.get('detail', '')}", flush=True)
            last_event[:] = [key, now]
    started = time.monotonic()
    result = run_benchmark(
        duration="5 months", earliest_allowed="2023-01-01", latest_allowed="2023-06-01",
        number_of_tests=1, random_seed=43, price_data=prices, progress_callback=progress,
    )
    elapsed = time.monotonic() - started
    (output / "pilot.zip").write_bytes(build_benchmark_zip(result))
    pilot = {"elapsed_seconds": elapsed, "valid": result.config["benchmark_valid"],
             "method_period_rows": len(result.period_table), "errors": result.errors,
             "coverage": result.data_coverage, "timings": result.config["stage_timings"],
             "leakage_audit": result.leakage_audit,
             "promotion": result.promotion_table.to_dict("records")}
    (output / "pilot.json").write_text(json.dumps(pilot, indent=2, default=_json_default), encoding="utf-8")
    print(f"COMPLETE {elapsed:.1f}s valid={pilot['valid']} rows={pilot['method_period_rows']} errors={result.errors}", flush=True)
    return 0 if pilot["valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
