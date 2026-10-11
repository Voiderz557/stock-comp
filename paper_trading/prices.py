"""Daily-close execution quotes for paper trading.

These helpers never call a broker and never claim to be real-time. They
extract the latest available daily Close from already-loaded price frames,
or fetch those frames through an injected `load_market_data` callable so
the dashboard and tests share one snapshot for execution and exposure.
"""

from datetime import datetime, time, timedelta, timezone

import pandas as pd

from paper_trading.portfolio import is_valid_market_price


SOURCE_LABEL = "latest available daily close (not real-time)"
EXECUTION_PRICE_LOOKBACK_CALENDAR_DAYS = 10
MARKET_TIMEZONE = "America/New_York"
REGULAR_MARKET_CLOSE = time(16, 0)


def _utc_timestamp(value):
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    return timestamp.tz_convert("UTC")


def latest_completed_close_date(now):
    """Last eligible daily-bar date at `now` in the New York market clock.

    Date-indexed bars are session labels, not quotes struck at midnight.
    Using the regular close also holds early-close bars until 16:00 ET.
    """
    market_now = _utc_timestamp(now).tz_convert(MARKET_TIMEZONE)
    session_date = market_now.date()
    if market_now.time() < REGULAR_MARKET_CLOSE:
        session_date -= timedelta(days=1)
    return session_date


def completed_daily_close_frame(frame, now, *, through_date=None):
    """Exclude future rows and daily bars whose session has not closed."""
    if frame is None or getattr(frame, "empty", True):
        return frame
    check_time = _utc_timestamp(now)
    # The index's own calendar date identifies its session. Converting a
    # date-labelled UTC midnight to New York would shift it to the prior day.
    session_days = pd.DatetimeIndex(pd.to_datetime(frame.index, errors="coerce"))
    session_days = session_days.tz_localize(None).normalize()
    row_instants = pd.to_datetime(frame.index, utc=True, errors="coerce")
    cutoff = pd.Timestamp(latest_completed_close_date(now))
    if through_date is not None:
        cutoff = min(cutoff, pd.Timestamp(through_date).tz_localize(None).normalize())
    eligible = (row_instants <= check_time) & (session_days <= cutoff)
    return frame.loc[eligible]


def _as_of_iso(index_value):
    timestamp = pd.Timestamp(index_value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    else:
        timestamp = timestamp.tz_convert("UTC")
    return timestamp.isoformat()


def latest_daily_close_quote(frame, *, now=None, through_date=None):
    """Return a Close quote, optionally restricting live bars and session dates."""
    if now is not None or through_date is not None:
        frame = completed_daily_close_frame(
            frame, now or datetime.now(timezone.utc), through_date=through_date
        )
    if frame is None or getattr(frame, "empty", True):
        return None
    if "Close" not in frame.columns:
        return None
    closes = frame["Close"].dropna()
    if closes.empty:
        return None
    price = float(closes.iloc[-1])
    if not is_valid_market_price(price):
        return None
    return {
        "price": price,
        "as_of": _as_of_iso(closes.index[-1]),
        "source_label": SOURCE_LABEL,
    }


def quotes_from_price_frames(
    price_data_by_ticker, required_tickers, *, now=None, through_date=None
):
    """Build a consistent quote snapshot for every required ticker.

    Raises ValueError if any required ticker is missing or invalid. Does
    not substitute scan/strategy prices or entry prices.
    """
    required = list(dict.fromkeys(required_tickers))
    missing = []
    quotes = {}
    frames = price_data_by_ticker or {}
    for ticker in required:
        quote = latest_daily_close_quote(
            frames.get(ticker), now=now, through_date=through_date
        )
        if quote is None:
            missing.append(ticker)
            continue
        quotes[ticker] = quote
    if missing:
        raise ValueError(
            "Cannot build an execution snapshot because a latest daily close "
            "is unavailable or invalid for: "
            + ", ".join(missing)
            + f". Prices are {SOURCE_LABEL}."
        )
    fetched_at = _utc_timestamp(now or datetime.now(timezone.utc)).isoformat()
    return {
        "prices": {ticker: quote["price"] for ticker, quote in quotes.items()},
        "quotes": quotes,
        "source_label": SOURCE_LABEL,
        "fetched_at": fetched_at,
    }


def fetch_execution_snapshot(
    tickers,
    load_market_data_fn,
    as_of_date=None,
    lookback_days=EXECUTION_PRICE_LOOKBACK_CALENDAR_DAYS,
    now=None,
):
    """Fetch one daily-close snapshot for `tickers` via `load_market_data_fn`.

    `load_market_data_fn(tickers, start, end)` must match
    `data.market_data.load_market_data` (returns `(data, cache_info)`).
    An empty ticker list returns an empty snapshot without calling the loader.
    """
    check_time = _utc_timestamp(now or datetime.now(timezone.utc))
    required = [ticker for ticker in dict.fromkeys(tickers) if ticker]
    if not required:
        return {
            "prices": {},
            "quotes": {},
            "source_label": SOURCE_LABEL,
            "fetched_at": check_time.isoformat(),
        }
    as_of = pd.Timestamp(latest_completed_close_date(check_time))
    if as_of_date is not None:
        requested = pd.Timestamp(as_of_date).tz_localize(None).normalize()
        as_of = min(as_of, requested)
    start = as_of - pd.Timedelta(days=lookback_days)
    price_data, _cache_info = load_market_data_fn(required, start, as_of)
    return quotes_from_price_frames(
        price_data, required, now=check_time, through_date=as_of
    )
