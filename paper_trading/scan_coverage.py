"""Auditable current-plan quote coverage; not historical index membership."""

from __future__ import annotations

import math

import pandas as pd

from data.ticker_history import get_ticker_identity
from paper_trading.prices import latest_daily_close_quote


def summarize_scan_coverage(universe, price_data, plan, minimum_price):
    """Classify each requested discovery symbol using only its dated quote.

    Counts are not necessarily disjoint: a provider history failure can coexist
    with a current quote. ``eligible`` means price/strategy data were sufficient
    for ranking, not that the security is competition-eligible or a BUY.
    """
    symbols = list(dict.fromkeys(universe))
    reference = plan.get("reference_quote_as_of")
    reference_day = pd.Timestamp(reference).date() if reference else None
    failures = {
        str(item.get("Ticker")) for item in (plan.get("cache_info") or {}).get("Data Source Failures") or []
    }
    unavailable = set(plan.get("unavailable_universe") or [])
    diagnostics = plan.get("candidate_diagnostics") or {}
    counts = {key: 0 for key in ("scanned", "quoted", "stale", "missing", "failed", "below_minimum", "eligible")}
    counts["scanned"] = len(symbols)
    issues = []
    for ticker in symbols:
        reasons = []
        quote = latest_daily_close_quote((price_data or {}).get(ticker))
        if quote is None:
            counts["missing"] += 1
            reasons.append("No dated positive closing quote")
        else:
            counts["quoted"] += 1
            quote_day = pd.Timestamp(quote["as_of"]).date()
            if reference_day and quote_day < reference_day:
                counts["stale"] += 1
                reasons.append(f"Quote {quote_day} precedes reference {reference_day}")
            price = float(quote["price"])
            if not math.isfinite(price) or price < minimum_price:
                counts["below_minimum"] += 1
                reasons.append(f"Price below selected ${minimum_price:g} minimum")
        if ticker in failures:
            counts["failed"] += 1
            reasons.append("Required provider history failed; see data warnings")
        if ticker in unavailable or diagnostics.get(ticker, {}).get("status") == "UNAVAILABLE":
            reasons.append("Strategy output unavailable")
            listing_start = get_ticker_identity(ticker).public_start
            if listing_start is not None:
                reasons.append(
                    f"Known listing start {listing_start.date()}; long-lookback history may be insufficient"
                )
        elif ticker not in diagnostics:
            reasons.append("No strategy ranking output for this symbol")
        if reasons:
            issues.append({"ticker": ticker, "reasons": reasons})
        else:
            counts["eligible"] += 1
    return {
        **counts,
        "reference_quote_as_of": str(reference_day) if reference_day else None,
        "issues": issues,
        "definition": "Coverage counts may overlap. Eligible means dated current quote above selected price floor and available strategy output; it is not a BUY or competition eligibility assertion.",
    }
