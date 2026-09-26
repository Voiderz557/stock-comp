"""Explain required-vs-actual historical coverage for paper-trading constituents.

This module never invents prices or drops tickers. It classifies each gap
so pre-listing warmup shortages are not confused with missing required
trading history.
"""

from __future__ import annotations

import pandas as pd

from data.historical_universe import HISTORICAL_UNIVERSE_START, get_membership_ranges
from data.ticker_history import (
    NOT_PUBLIC_YET,
    get_provider_symbol,
    get_ticker_identity,
    lifecycle_status_for_period,
    listed_price_range,
)

PRE_LISTING_WARMUP = "pre-listing warmup shortage"
MISSING_REQUIRED_HISTORY = "missing required trading history"
PROVIDER_SYMBOL = "provider-symbol / routed history issue"
INTERNAL_GAP = "internal gap in cached rows"
COVERED = "required trading history present"
NOT_REQUIRED = "requested range is outside required membership"


def _frame_coverage(frame, start, end):
    if frame is None or getattr(frame, "empty", True) or "Close" not in frame.columns:
        return {
            "Available Start": None,
            "Available End": None,
            "Row Count": 0,
            "Rows In Range": 0,
        }
    closes = frame["Close"].dropna()
    in_range = closes.loc[(closes.index >= start) & (closes.index <= end)]
    return {
        "Available Start": None if closes.empty else str(closes.index.min().date()),
        "Available End": None if closes.empty else str(closes.index.max().date()),
        "Row Count": int(len(closes)),
        "Rows In Range": int(len(in_range)),
    }


def classify_coverage_gap(ticker, start, end, frame=None):
    """Classify one inclusive [start, end] window for `ticker`."""
    start = pd.Timestamp(start).normalize()
    end = pd.Timestamp(end).normalize()
    identity = get_ticker_identity(ticker)
    lifecycle = lifecycle_status_for_period(ticker, start, end)
    listed = listed_price_range(ticker, start, end + pd.Timedelta(days=1))
    if lifecycle == NOT_PUBLIC_YET or listed is None:
        return PRE_LISTING_WARMUP
    if identity.public_start is not None and end < identity.public_start:
        return PRE_LISTING_WARMUP
    if identity.public_start is not None and start < identity.public_start:
        return PRE_LISTING_WARMUP
    if identity.split_provider_history:
        return PROVIDER_SYMBOL
    coverage = _frame_coverage(frame, start, end)
    if coverage["Rows In Range"] == 0:
        return MISSING_REQUIRED_HISTORY
    in_range = frame["Close"].dropna()
    in_range = in_range.loc[(in_range.index >= start) & (in_range.index <= end)]
    if in_range.empty:
        return MISSING_REQUIRED_HISTORY
    start_gap = (in_range.index.min() - start).days
    end_gap = (end - in_range.index.max()).days
    if start_gap <= 7 and end_gap <= 7:
        return COVERED
    return INTERNAL_GAP


def diagnose_ticker_coverage(ticker, required_ranges, price_data=None, failures=None):
    """Return structured rows for each required membership range of `ticker`."""
    identity = get_ticker_identity(ticker)
    frame = (price_data or {}).get(ticker)
    failure_reasons = [
        item.get("Reason")
        for item in (failures or [])
        if item.get("Ticker") == ticker
    ]
    rows = []
    for start, end in required_ranges or []:
        start = pd.Timestamp(start).normalize()
        end = pd.Timestamp(end).normalize()
        coverage = _frame_coverage(frame, start, end)
        classification = classify_coverage_gap(ticker, start, end, frame)
        if coverage["Rows In Range"] > 0 and classification == MISSING_REQUIRED_HISTORY:
            classification = classify_coverage_gap(ticker, start, end, frame)
        rows.append(
            {
                "Ticker": ticker,
                "Required Start": str(start.date()),
                "Required End": str(end.date()),
                "Actual Start": coverage["Available Start"],
                "Actual End": coverage["Available End"],
                "Rows In Required Range": coverage["Rows In Range"],
                "Cached Rows": coverage["Row Count"],
                "Classification": classification,
                "Lifecycle": identity.lifecycle_status,
                "Provider Symbol": get_provider_symbol(ticker),
                "Public Start": (
                    None
                    if identity.public_start is None
                    else str(pd.Timestamp(identity.public_start).date())
                ),
                "Public End": (
                    None
                    if identity.public_end is None
                    else str(pd.Timestamp(identity.public_end).date())
                ),
                "Failure Reasons": " | ".join(failure_reasons),
            }
        )
    return rows


def diagnose_coverage_failures(failures, evaluation_start, evaluation_end, price_data=None):
    """Diagnose every ticker mentioned in loader failures against membership."""
    evaluation_start = pd.Timestamp(evaluation_start).normalize()
    evaluation_end = pd.Timestamp(evaluation_end).normalize()
    membership = {}
    membership_start = max(evaluation_start, HISTORICAL_UNIVERSE_START)
    membership_end = max(evaluation_end, HISTORICAL_UNIVERSE_START)
    if membership_start <= membership_end:
        try:
            membership = get_membership_ranges(membership_start, membership_end)
        except ValueError:
            membership = {}
    tickers = sorted({item.get("Ticker") for item in (failures or []) if item.get("Ticker")})
    rows = []
    for ticker in tickers:
        required = membership.get(ticker)
        if not required:
            for item in failures:
                if item.get("Ticker") != ticker:
                    continue
                rows.append(
                    {
                        "Ticker": ticker,
                        "Required Start": item.get("Requested Start"),
                        "Required End": item.get("Requested End"),
                        "Actual Start": None,
                        "Actual End": None,
                        "Rows In Required Range": 0,
                        "Cached Rows": 0,
                        "Classification": NOT_REQUIRED,
                        "Lifecycle": get_ticker_identity(ticker).lifecycle_status,
                        "Provider Symbol": get_provider_symbol(ticker),
                        "Public Start": None,
                        "Public End": None,
                        "Failure Reasons": item.get("Reason", ""),
                    }
                )
            continue
        rows.extend(
            diagnose_ticker_coverage(ticker, required, price_data=price_data, failures=failures)
        )
    return rows
