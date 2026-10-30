"""Read-only, dated VIX context; never used as realized-volatility input."""

from datetime import date, datetime, timedelta, timezone
import math

import pandas as pd


VIX_SYMBOL = "^VIX"
VIX_LOOKBACK_DAYS = 14
VIX_MAX_AGE_DAYS = 5


def summarize_vix(frame: pd.DataFrame | None, *, as_of: date) -> dict:
    """Use the last valid close on/before as_of; never substitute a zero quote."""
    result = {"symbol": "VIX", "value": None, "price_as_of": None,
              "status": "UNAVAILABLE", "warning": "A dated VIX close is unavailable."}
    if frame is None or frame.empty or "Close" not in frame:
        return result
    closes = pd.to_numeric(frame["Close"], errors="coerce").sort_index()
    dates = pd.to_datetime(closes.index, utc=True).date
    closes = closes.loc[dates <= as_of]
    closes = closes[closes.map(lambda value: math.isfinite(value) and value > 0)]
    if closes.empty:
        return result
    dated = pd.Timestamp(closes.index[-1]).date()
    result["value"] = round(float(closes.iloc[-1]), 2)
    result["price_as_of"] = dated.isoformat()
    result["status"] = "CURRENT" if (as_of - dated).days <= VIX_MAX_AGE_DAYS else "STALE"
    result["warning"] = None if result["status"] == "CURRENT" else "The latest VIX close is stale; do not treat it as today's reading."
    return result


def load_vix_context(*, as_of: date | None = None, loader=None, now=None) -> dict:
    if loader is None:
        from data.market_data import load_market_data
        loader = load_market_data
    live = as_of is None
    observed_at = now or datetime.now(timezone.utc)
    as_of = as_of or pd.Timestamp(observed_at).tz_convert("America/New_York").date()
    try:
        prices, _ = loader([VIX_SYMBOL], as_of - timedelta(days=VIX_LOOKBACK_DAYS), as_of)
        frame = prices.get(VIX_SYMBOL)
        if live:
            # Daily provider bars can include the still-open session. Use the
            # same completed-close rule as live plans, without changing signals.
            from paper_trading.prices import completed_daily_close_frame
            frame = completed_daily_close_frame(frame, observed_at)
        return summarize_vix(frame, as_of=as_of)
    except Exception:
        # Data-provider failures do not alter regime classification or plans.
        return summarize_vix(None, as_of=as_of)
