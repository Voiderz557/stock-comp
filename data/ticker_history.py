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
    note: str = ""


# This registry is deliberately independent of Nasdaq-100 membership. It tells
# providers how a historical security relates to symbols available today.
TICKER_IDENTITIES = {
    "EA": TickerIdentity(
        lifecycle_status=DELISTED_LATER,
        note="Historically valid EA security; later acquisition/delisting.",
    ),
    "ATVI": TickerIdentity(lifecycle_status=DELISTED_LATER),
    "SGEN": TickerIdentity(lifecycle_status=DELISTED_LATER),
    "SPLK": TickerIdentity(lifecycle_status=DELISTED_LATER),
    "ANSS": TickerIdentity(lifecycle_status=DELISTED_LATER),
    "FB": TickerIdentity(
        lifecycle_status=SYMBOL_CHANGE,
        provider_symbol="META",
        note="Facebook changed its trading symbol from FB to META in 2022.",
    ),
    "FISV": TickerIdentity(
        lifecycle_status=SYMBOL_CHANGE,
        provider_symbol="FI",
        note="Fiserv changed its trading symbol from FISV to FI in 2023.",
    ),
    "ARM": TickerIdentity(public_start=pd.Timestamp("2023-09-14")),
    "ALAB": TickerIdentity(public_start=pd.Timestamp("2024-03-20")),
    "CRWV": TickerIdentity(public_start=pd.Timestamp("2025-03-28")),
}


def get_ticker_identity(ticker):
    return TICKER_IDENTITIES.get(ticker, TickerIdentity())


def get_provider_symbol(ticker):
    return get_ticker_identity(ticker).provider_symbol or ticker


def lifecycle_status_for_period(ticker, start_date, end_date):
    identity = get_ticker_identity(ticker)
    if identity.public_start is not None and pd.Timestamp(end_date) < identity.public_start:
        return NOT_PUBLIC_YET
    return identity.lifecycle_status
