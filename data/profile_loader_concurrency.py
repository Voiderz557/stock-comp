"""Compare cold sequential vs bounded-parallel current-price loading.

Uses two independent temporary caches; never touches portfolio data or the
ordinary price cache. This is a market-data timing probe, not a backtest.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path
from time import perf_counter

import pandas as pd
import numpy as np

from data.market_data import load_market_data
from data.scanner_universe import BROAD_CURRENT_SCANNER_UNIVERSE


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample-size", type=int, default=24)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    if not 2 <= args.sample_size <= len(BROAD_CURRENT_SCANNER_UNIVERSE):
        parser.error("sample-size must be between 2 and the broad-watchlist size")
    indices = [round(index * (len(BROAD_CURRENT_SCANNER_UNIVERSE) - 1) / (args.sample_size - 1)) for index in range(args.sample_size)]
    symbols = [BROAD_CURRENT_SCANNER_UNIVERSE[index] for index in indices]
    end = pd.Timestamp.today().normalize()
    start = end - pd.Timedelta(days=450)
    results = {}
    with tempfile.TemporaryDirectory(prefix="stock-comp-loader-profile-") as root:
        for label, workers in (("sequential", 1), ("parallel", args.workers)):
            started = perf_counter()
            frames, info = load_market_data(
                symbols, start, end, cache_dir=Path(root) / label,
                max_workers=workers,
                status_callback=lambda stage, detail: print(f"{label}: {stage} {detail}", flush=True) if int(detail.split("(")[-1].split("/")[0]) % 8 == 0 else None,
            )
            results[label] = (frames, info)
            print(json.dumps({
                "pass": label, "elapsed_seconds": round(perf_counter() - started, 3),
                "symbols": len(symbols),
                "quoted": sum(not frame.empty for frame in frames.values()),
                "failures": sorted({item["Ticker"] for item in info["Data Source Failures"]}),
            }), flush=True)
        sequential, parallel = results["sequential"][0], results["parallel"][0]
        differences = [ticker for ticker in symbols if not sequential[ticker].equals(parallel[ticker])]
        material = []
        for ticker in differences:
            left, right = sequential[ticker], parallel[ticker]
            if left.empty or right.empty:
                if left.empty != right.empty:
                    material.append(ticker)
                continue
            if (not left.index.equals(right.index) or list(left.columns) != list(right.columns)
                or not left["Volume"].equals(right["Volume"])
                or any(not np.allclose(left[column], right[column], rtol=1e-6, atol=1e-4, equal_nan=True)
                       for column in ("Open", "High", "Low", "Close"))):
                material.append(ticker)
        print(json.dumps({
            "same_symbol_order": list(sequential) == list(parallel),
            "exact_frame_differences": differences,
            "material_frame_differences_at_0.0001_price_tolerance": material,
        }), flush=True)
        for ticker in differences[:8]:
            left, right = sequential[ticker], parallel[ticker]
            shared = left.index.intersection(right.index)
            close_difference = (
                float((left.loc[shared, "Close"] - right.loc[shared, "Close"]).abs().max())
                if len(shared) and "Close" in left and "Close" in right else None
            )
            print(json.dumps({
                "ticker": ticker, "sequential_rows": len(left), "parallel_rows": len(right),
                "sequential_last": str(left.index.max()) if not left.empty else None,
                "parallel_last": str(right.index.max()) if not right.empty else None,
                "sequential_last_close": float(left["Close"].iloc[-1]) if not left.empty else None,
                "parallel_last_close": float(right["Close"].iloc[-1]) if not right.empty else None,
                "max_shared_close_difference": close_difference,
                "sequential_dtypes": {key: str(value) for key, value in left.dtypes.items()},
                "parallel_dtypes": {key: str(value) for key, value in right.dtypes.items()},
            }), flush=True)


if __name__ == "__main__":
    main()
