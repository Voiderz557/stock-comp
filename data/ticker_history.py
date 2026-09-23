"""Point-in-time ticker identity metadata, separate from index membership."""

from dataclasses import dataclass

import pandas as pd


NOT_PUBLIC_YET = "NOT PUBLIC YET"
DELISTED_LATER = "DELISTED LATER BUT VALID FOR THIS PERIOD"
MISSING_FROM_PROVIDER = "MISSING DATA FROM PROVIDER"
SYMBOL_CHANGE = "TICKER SYMBOL CHANGE"
HISTORICALLY_VALID = "HISTORICALLY VALID CONSTITUENT"


@dataclass(frozen=True)
class TickerIdentity:
    lifecycle_status: str = HISTORICALLY_VALID
    provider_symbol: str | None = None
    public_start: pd.Timestamp | None = None
    public_end: pd.Timestamp | None = None
    symbol_change_date: pd.Timestamp | None = None
    split_provider_history: bool = False
    note: str = ""


# This registry is deliberately independent of Nasdaq-100 membership. It tells
# providers how a historical security relates to symbols available today.
# public_start / public_end are last-known listing boundaries used only to
# avoid requesting impossible Yahoo ranges. They never remove a historically
# valid constituent from a universe or backtest.
TICKER_IDENTITIES = {
    "EA": TickerIdentity(
        lifecycle_status=DELISTED_LATER,
        note="Historically valid EA security; later acquisition/delisting.",
    ),
    "ATVI": TickerIdentity(
        lifecycle_status=DELISTED_LATER,
        public_end=pd.Timestamp("2023-10-13"),
        note="Microsoft acquisition; last regular-session trade 2023-10-13.",
    ),
    "SGEN": TickerIdentity(
        lifecycle_status=DELISTED_LATER,
        public_end=pd.Timestamp("2023-12-14"),
        note="Pfizer acquisition; last regular-session trade 2023-12-14.",
    ),
    "SPLK": TickerIdentity(
        lifecycle_status=DELISTED_LATER,
        public_end=pd.Timestamp("2024-03-18"),
        note="Cisco acquisition; last regular-session trade 2024-03-18.",
    ),
    "ANSS": TickerIdentity(
        lifecycle_status=DELISTED_LATER,
        public_end=pd.Timestamp("2025-07-16"),
        note="Synopsys acquisition; last regular-session trade 2025-07-16.",
    ),
    "FB": TickerIdentity(
        lifecycle_status=SYMBOL_CHANGE,
        provider_symbol="META",
        symbol_change_date=pd.Timestamp("2022-06-09"),
        note="Facebook changed its trading symbol from FB to META in 2022. "
        "Yahoo's META series covers the pre-rename history, so requests are "
        "not split across FB and META.",
    ),
    "FISV": TickerIdentity(
        lifecycle_status=SYMBOL_CHANGE,
        provider_symbol="FI",
        symbol_change_date=pd.Timestamp("2023-06-07"),
        split_provider_history=True,
        note="Fiserv changed its trading symbol from FISV to FI in 2023. "
        "Yahoo's FI series does not cover pre-rename dates, so those ranges "
        "are requested as FISV.",
    ),
    "FI": TickerIdentity(
        lifecycle_status=SYMBOL_CHANGE,
        public_start=pd.Timestamp("2023-06-07"),
        note="Fiserv current ticker; public as FI from the 2023 symbol change.",
    ),
    "CEG": TickerIdentity(
        public_start=pd.Timestamp("2022-01-19"),
        note="Constellation Energy regular-way trading began 2022-01-19 after the Exelon spin-off.",
    ),
    "DASH": TickerIdentity(
        public_start=pd.Timestamp("2020-12-09"),
        note="DoorDash IPO; first regular-session trade 2020-12-09.",
    ),
    "GEHC": TickerIdentity(
        public_start=pd.Timestamp("2023-01-04"),
        note="GE HealthCare regular-way trading began 2023-01-04 after the GE spin-off.",
    ),
    "GFS": TickerIdentity(
        public_start=pd.Timestamp("2021-10-28"),
        note="GlobalFoundries IPO; first regular-session trade 2021-10-28.",
    ),
    "ARM": TickerIdentity(public_start=pd.Timestamp("2023-09-14")),
    "ALAB": TickerIdentity(public_start=pd.Timestamp("2024-03-20")),
    "CRWV": TickerIdentity(public_start=pd.Timestamp("2025-03-28")),
    "WBA": TickerIdentity(
        lifecycle_status=DELISTED_LATER,
        public_end=pd.Timestamp("2025-08-27"),
        note="Walgreens Boots Alliance; last regular-session trade 2025-08-27.",
    ),
}


def get_ticker_identity(ticker):
    return TICKER_IDENTITIES.get(ticker, TickerIdentity())


def get_provider_symbol(ticker):
    return get_ticker_identity(ticker).provider_symbol or ticker


def provider_fetch_segments(ticker, start, end_exclusive):
    """Yahoo symbol/date segments for `[start, end_exclusive)`.

    Listing boundaries are applied by the caller via `listed_price_range`.
    A symbol change is split only when the new Yahoo series does not cover
    the pre-rename history (`split_provider_history=True`).
    """
    start = pd.Timestamp(start).normalize()
    end_exclusive = pd.Timestamp(end_exclusive).normalize()
    if start >= end_exclusive:
        return []
    identity = get_ticker_identity(ticker)
    current_symbol = get_provider_symbol(ticker)
    change = identity.symbol_change_date
    if (
        not identity.split_provider_history
        or change is None
        or not identity.provider_symbol
    ):
        return [(current_symbol, start, end_exclusive)]
    change = pd.Timestamp(change).normalize()
    segments = []
    if start < change:
        segments.append((ticker, start, min(end_exclusive, change)))
    if end_exclusive > change:
        segments.append(
            (identity.provider_symbol, max(start, change), end_exclusive)
        )
    return [(symbol, left, right) for symbol, left, right in segments if left < right]


def lifecycle_status_for_period(ticker, start_date, end_date):
    identity = get_ticker_identity(ticker)
    start_date = pd.Timestamp(start_date).normalize()
    end_date = pd.Timestamp(end_date).normalize()
    if identity.public_start is not None and end_date < identity.public_start:
        return NOT_PUBLIC_YET
    if identity.public_end is not None and start_date > identity.public_end:
        return identity.lifecycle_status
    return identity.lifecycle_status


def listed_price_range(ticker, start, end_exclusive):
    """Clip `[start, end_exclusive)` to known public trading dates.

    Returns None when the entire window is before listing or after the last
    known public date. This is a listing-boundary check only: a generic
    Yahoo empty/timeout response must never be treated as proof that a
    ticker is permanently unavailable on other dates.
    """
    identity = get_ticker_identity(ticker)
    start = pd.Timestamp(start).normalize()
    end_exclusive = pd.Timestamp(end_exclusive).normalize()
    if start >= end_exclusive:
        return None
    if identity.public_start is not None:
        listed_start = pd.Timestamp(identity.public_start).normalize()
        if end_exclusive <= listed_start:
            return None
        start = max(start, listed_start)
    if identity.public_end is not None:
        listed_end_exclusive = pd.Timestamp(identity.public_end).normalize() + pd.Timedelta(days=1)
        if start >= listed_end_exclusive:
            return None
        end_exclusive = min(end_exclusive, listed_end_exclusive)
    if start >= end_exclusive:
        return None
    return start, end_exclusive
