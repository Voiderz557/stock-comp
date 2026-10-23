"""Pure outcome research over the original, caller-authorized cloud history.

Integration: call build_recommendation_journal(load_plan_history(...)) first.
On an explicit refresh only, pass its fetch_request to data.market_data's
load_market_data(tickers, start_date, end_date), which reuses the existing cache.
Pass the returned price dictionary (not the loader's report tuple) back here.
No loader, account store, portfolio, strategy, or network is invoked here.

Close is the existing loader's adjusted daily Close. Returns are fractions,
not percentages, fills, strategy returns, or trading P&L. In particular SELL
and AVOID rows report the same underlying stock return as BUY rows.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, time, timedelta, timezone
import math
import re

import pandas as pd

HORIZONS = (5, 20, 60)
MAX_SYMBOLS = 50  # Includes SPY; excess recommendations are retained, not dropped.
MARKET_TZ = "America/New_York"
CALENDAR_SOURCE = "https://www.nyse.com/trade/hours-calendars"
# Published NYSE full-day closures, including the 2025 Carter mourning day.
# Deliberately bounded: extending coverage requires checking the exchange's
# published calendar, including exceptional closures. Early closes are sessions.
_HOLIDAYS = {
    2025: "01-01 01-09 01-20 02-17 04-18 05-26 06-19 07-04 09-01 11-27 12-25",
    2026: "01-01 01-19 02-16 04-03 05-25 06-19 07-03 09-07 11-26 12-25",
    2027: "01-01 01-18 02-15 03-26 05-31 06-18 07-05 09-06 11-25 12-24",
    2028: "01-17 02-21 04-14 05-29 06-19 07-04 09-04 11-23 12-25",
}
_CLOSED = {date.fromisoformat(f"{year}-{day}")
           for year, days in _HOLIDAYS.items() for day in days.split()}


def _now():
    return datetime.now(timezone.utc)


def _cutoff(asof):
    """Historical dates mean end of day; today is clamped to completed bars.

    Even early-close sessions are conservatively withheld until 16:00 ET.
    Future asof values cannot unlock future or unfinished daily bars.
    """
    now = pd.Timestamp(_now()).tz_convert(MARKET_TZ)
    completed = now.date() - timedelta(days=now.time() < time(16))
    if asof is None:
        return completed
    requested = pd.Timestamp(asof)
    if pd.isna(requested):
        raise ValueError("asof must be a valid date")
    return min(requested.date(), completed)


def _signal_date(value):
    # market_as_of is a daily-bar label; preserve its date, not a UTC->ET shift.
    try:
        stamp = pd.Timestamp(value)
        return None if pd.isna(stamp) else stamp.date()
    except (TypeError, ValueError):
        return None


def _generation_date(value):
    # Persisted generated_at is timestamptz. Unknown timezone must not invent
    # a publication time. Missing/naive timestamps invalidate the outcome.
    try:
        stamp = pd.Timestamp(value)
        if pd.isna(stamp) or stamp.tzinfo is None:
            return None
        return stamp.tz_convert(MARKET_TZ).date()
    except (TypeError, ValueError):
        return None


def _sessions_after(day):
    if day.year not in _HOLIDAYS:
        return []
    result = []
    while len(result) <= max(HORIZONS):
        day += timedelta(days=1)
        if day.year not in _HOLIDAYS:
            break
        if day.weekday() < 5 and day not in _CLOSED:
            result.append(day)
    return result


def _prices(frame, cutoff):
    """Keep exact session labels; never fill gaps or replace an invalid close."""
    if frame is None or not isinstance(frame, pd.DataFrame) or frame.empty:
        return {}, "missing_history"
    if "Close" not in frame or not isinstance(frame.index, pd.DatetimeIndex):
        return {}, "invalid_history"
    days = frame.index.date
    eligible = [(day, value) for day, value in zip(days, frame["Close"])
                if not pd.isna(day) and day <= cutoff]
    if len({day for day, _ in eligible}) != len(eligible):
        return {}, "duplicate_session"
    result = {}
    for day, value in eligible:
        try:
            number = float(value)
            result[day] = number if math.isfinite(number) and number > 0 else None
        except (TypeError, ValueError):
            result[day] = None
    return result, None


def _return(prices, problem, sessions):
    if problem:
        return None, problem
    for day in sessions:
        if day not in prices:
            return None, f"missing_session:{day.isoformat()}"
        if prices[day] is None:
            return None, f"invalid_close:{day.isoformat()}"
    value = prices[sessions[-1]] / prices[sessions[0]] - 1
    if not math.isfinite(value):
        return None, "invalid_return"
    return value, None


def build_recommendation_journal(
    history_rows, price_history_by_ticker=None, asof=None, *, max_symbols=MAX_SYMBOLS,
):
    """Return a JSON-compatible research journal without mutating inputs.

    history_rows is exactly load_plan_history's list of plans with nested
    recommendations. Every supplied row/status is retained in source_history
    and entries; it is never rescored, replaced, universe-filtered, or deduped.
    Results describe only this supplied history window, not a complete cohort.

    Reference = first NYSE session strictly after BOTH the recorded market date
    and the generation date in New York. Even pre-close generation waits until
    the following session. Horizon H ends H sessions after that reference.
    Require all H+1 exact closes for each series independently. SPY and stock
    always use identical endpoints; missing SPY never changes the horizon.
    """
    if isinstance(max_symbols, bool) or not isinstance(max_symbols, int) or not 1 <= max_symbols <= MAX_SYMBOLS:
        raise ValueError(f"max_symbols must be between 1 and {MAX_SYMBOLS}")
    history = deepcopy(list(history_rows))
    cutoff = _cutoff(asof)
    symbols = sorted({str(rec.get("ticker", "")).strip().upper()
                      for plan in history for rec in plan.get("recommendations", [])
                      if re.fullmatch(r"[A-Za-z0-9.^-]{1,32}", str(rec.get("ticker", "")).strip())})
    needed = (["SPY"] + [ticker for ticker in symbols if ticker != "SPY"]) if symbols else []
    selected, deferred = needed[:max_symbols], needed[max_symbols:]
    supplied = price_history_by_ticker if price_history_by_ticker is not None else {}
    parsed = {symbol: _prices(supplied.get(symbol), cutoff) for symbol in selected}
    entries, starts = [], []
    for plan in history:
        signal = _signal_date(plan.get("market_as_of"))
        generated = _generation_date(plan.get("generated_at"))
        problem = None
        if signal is None or generated is None:
            problem = "invalid_recorded_timing"
        elif signal > generated:
            problem = "signal_date_after_generation"
        elif max(signal, generated) > cutoff:
            problem = "generation_not_yet_observable"
        sessions = _sessions_after(max(signal, generated)) if problem is None else []
        if not sessions and problem is None:
            problem = "calendar_unavailable"
        if sessions and sessions[0] <= cutoff:
            starts.append(sessions[0])
        for position, rec in enumerate(plan.get("recommendations", [])):
            ticker = str(rec.get("ticker", "")).strip().upper()
            row_problem = problem or ("symbol_limit" if ticker in deferred else
                                      "invalid_ticker" if ticker not in selected else None)
            outcomes = []
            for horizon in HORIZONS:
                end = sessions[horizon] if len(sessions) > horizon else None
                reason = row_problem
                if reason is None:
                    reason = "calendar_unavailable" if end is None else "maturing" if end > cutoff else None
                stock_return = spy_return = None
                stock_reason = spy_reason = reason
                if reason is None:
                    window = sessions[:horizon + 1]
                    stock_return, stock_reason = _return(*parsed[ticker], window)
                    spy_return, spy_reason = _return(*parsed["SPY"], window)
                paired = stock_return is not None and spy_return is not None
                outcomes.append({
                    "sessions": horizon,
                    "end_date": end.isoformat() if end else None,
                    "stock_return": stock_return, "spy_return": spy_return,
                    "excess_return": stock_return - spy_return if paired else None,
                    "stock_null_reason": stock_reason, "spy_null_reason": spy_reason,
                    "null_reason": stock_reason or spy_reason,
                    "status": "complete" if paired else "maturing" if reason == "maturing" else "unavailable",
                })
            entries.append({
                "plan_id": plan.get("id"), "recommendation_index": position,
                "strategy": plan.get("strategy"), "plan_status": plan.get("status"),
                "generated_at": plan.get("generated_at"), "market_as_of": plan.get("market_as_of"),
                "ticker": ticker, "status": rec.get("status"), "action": rec.get("action"),
                "recommendation": deepcopy(rec),
                "signal_date": signal.isoformat() if signal else None,
                "reference_date": sessions[0].isoformat() if sessions else None,
                "outcomes": outcomes,
            })
    return {
        "asof": cutoff.isoformat(), "source_history": history, "entries": entries,
        "needed_symbols": needed, "deferred_symbols": deferred,
        "fetch_request": {
            "tickers": selected,
            "start_date": min(starts).isoformat(), "end_date": cutoff.isoformat(),
        } if starts and selected else None,
        "methodology": {
            "kind": "post_publication_close_research_return",
            "return_units": "fraction", "price_basis": "loader_adjusted_close",
            "reference": "First NYSE session close strictly after signal and generation dates; H sessions thereafter.",
            "signal_date_return": "Not calculated: signal-date close may predate publication.",
            "execution": "Research reference only; no fills, fees, slippage, sizing, or trading P&L.",
            "calendar_source": CALENDAR_SOURCE,
            "calendar_years": sorted(_HOLIDAYS),
            "coverage": "Only supplied saved history; all actions/statuses retained, including unavailable symbols.",
            "bias_warning": "Not a strategy backtest or unbiased performance estimate. Truncated history, missing prices, adjusted-price revisions, and unavailable delisting returns prevent that claim. Never average only complete rows as strategy performance.",
        },
    }
