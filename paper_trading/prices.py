"""Daily-close execution quotes for paper trading.

These helpers never call a broker and never claim to be real-time. They
extract the latest available daily Close from already-loaded price frames,
or fetch those frames through an injected `load_market_data` callable so
the dashboard and tests share one snapshot for execution and exposure.
"""

from datetime import datetime, timezone

import pandas as pd

from paper_trading.portfolio import is_valid_market_price


SOURCE_LABEL = "latest available daily close (not real-time)"
EXECUTION_PRICE_LOOKBACK_CALENDAR_DAYS = 10


def _as_of_iso(index_value):
    timestamp = pd.Timestamp(index_value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    else:
        timestamp = timestamp.tz_convert("UTC")
    return timestamp.isoformat()


def latest_daily_close_quote(frame):
    """Return {price, as_of, source_label} from a Close series, or None."""
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


def quotes_from_price_frames(price_data_by_ticker, required_tickers):
    """Build a consistent quote snapshot for every required ticker.

    Raises ValueError if any required ticker is missing or invalid. Does
    not substitute scan/strategy prices or entry prices.
    """
    required = list(dict.fromkeys(required_tickers))
    missing = []
    quotes = {}
    frames = price_data_by_ticker or {}
    for ticker in required:
        quote = latest_daily_close_quote(frames.get(ticker))
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
    fetched_at = datetime.now(timezone.utc).isoformat()
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
):
    """Fetch one daily-close snapshot for `tickers` via `load_market_data_fn`.

    `load_market_data_fn(tickers, start, end)` must match
    `data.market_data.load_market_data` (returns `(data, cache_info)`).
    An empty ticker list returns an empty snapshot without calling the loader.
    """
    required = [ticker for ticker in dict.fromkeys(tickers) if ticker]
    if not required:
        return {
            "prices": {},
            "quotes": {},
            "source_label": SOURCE_LABEL,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }
    as_of = pd.Timestamp(as_of_date).normalize() if as_of_date is not None else pd.Timestamp.today().normalize()
    start = as_of - pd.Timedelta(days=lookback_days)
    price_data, _cache_info = load_market_data_fn(required, start, as_of)
    return quotes_from_price_frames(price_data, required)
