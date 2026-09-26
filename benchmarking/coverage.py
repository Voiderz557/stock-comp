"""Read-only coverage checks for supplied and downloaded benchmark inputs.

Uses the benchmark's session calendar, not weekday guesses. Membership is
resolved only for observation dates; price warmup is never a membership date.
This checks availability, not corporate-action accuracy or data provenance.
"""

import numpy as np
import pandas as pd

from data.historical_universe import get_membership_ranges
from data.ticker_history import listed_price_range
from backtesting.engine import benchmark_coverage_error


def audit_price_coverage(price_data, start_date, end_date, universe=None, benchmark="SPY"):
    start, end = pd.Timestamp(start_date), pd.Timestamp(end_date)
    ranges = (
        get_membership_ranges(start, end)
        if universe is None
        else {ticker: [(start, end)] for ticker in universe}
    )
    ranges = dict(ranges)
    for ticker in {benchmark, "SPY", "QQQ"}:
        ranges[ticker] = [(start, end)]
    reference = price_data.get(benchmark)
    calendar = pd.DatetimeIndex([])
    if reference is not None and isinstance(reference.index, pd.DatetimeIndex):
        calendar = reference.index[(reference.index >= start) & (reference.index <= end)]
    calendar_problem = (
        benchmark_coverage_error(reference, start, end)
        if reference is not None and isinstance(reference.index, pd.DatetimeIndex)
        else "Benchmark trading calendar unavailable"
    )
    failures = []
    for ticker, windows in sorted(ranges.items()):
        frame = price_data.get(ticker)
        for first, last in windows:
            listed = listed_price_range(ticker, first, last + pd.Timedelta(days=1))
            if listed is None:
                continue
            first, exclusive_end = listed
            expected = calendar[(calendar >= first) & (calendar < exclusive_end)]
            reason = None
            if frame is None or frame.empty:
                reason = "No price rows for required historical constituent/context"
            elif not isinstance(frame.index, pd.DatetimeIndex) or frame.index.has_duplicates:
                reason = "Price index must contain unique trading timestamps"
            elif not {"Open", "Close"}.issubset(frame.columns):
                reason = "Open/Close prices missing"
            elif calendar.empty or calendar_problem:
                reason = f"Benchmark trading calendar cannot be verified: {calendar_problem}"
            elif len(expected):
                prices = frame.reindex(expected)[["Open", "Close"]]
                numeric = prices.apply(pd.to_numeric, errors="coerce")
                bad = (~np.isfinite(numeric) | (numeric <= 0)).any(axis=1)
                if bad.any():
                    reason = f"Missing/invalid Open or Close on {int(bad.sum())} benchmark sessions; first {expected[bad][0].date()}"
            if reason:
                failures.append({
                    "Ticker": ticker, "Requested Start": str(first.date()),
                    "Requested End": str((exclusive_end - pd.Timedelta(days=1)).date()),
                    "Provider": "input coverage audit", "Reason": reason,
                })
    return {
        "Data Source Failures": failures,
        "Unavailable Valid Constituents": sorted({row["Ticker"] for row in failures}),
        "Coverage Is Valid": not failures,
    }
