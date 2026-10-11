"""Current liquid stock universes used by the scanner only.

The seed list is retained for historical/research callers. Only the assembled
current-day scanner lists omit securities past a verified last trading date.
"""

from datetime import date

from data.ticker_history import get_ticker_identity


def current_scanner_symbols(symbols, *, as_of=None):
    """Filter known listing boundaries for live discovery, not historical membership."""
    as_of = as_of or date.today()
    eligible = []
    for ticker in dict.fromkeys(symbols):
        identity = get_ticker_identity(ticker)
        if identity.public_start is not None and as_of < identity.public_start.date():
            continue
        if identity.public_end is not None and as_of > identity.public_end.date():
            continue
        eligible.append(ticker)
    return eligible


# Nasdaq-100 test universe, updated for the June 2026 announced changes.
# This must be replaced or filtered after competition eligibility is confirmed.
NASDAQ_100_TEST_UNIVERSE = [
    "AAPL",
    "ABNB",
    "ADBE",
    "ADI",
    "ADP",
    "ADSK",
    "AEP",
    "ALAB",
    "ALNY",
    "AMAT",
    "AMD",
    "AMGN",
    "AMZN",
    "APP",
    "ARM",
    "ASML",
    "AVGO",
    "AXON",
    "BKNG",
    "BKR",
    "CCEP",
    "CDNS",
    "CEG",
    "CMCSA",
    "COST",
    "CPRT",
    "CRWD",
    "CRWV",
    "CSCO",
    "CSGP",
    "CSX",
    "CTAS",
    "DASH",
    "DDOG",
    "DXCM",
    "EXC",
    "FANG",
    "FAST",
    "FER",
    "FTNT",
    "GEHC",
    "GILD",
    "GOOG",
    "GOOGL",
    "HON",
    "IDXX",
    "INTC",
    "INTU",
    "ISRG",
    "KDP",
    "KHC",
    "KLAC",
    "LIN",
    "LRCX",
    "MAR",
    "MCHP",
    "MDLZ",
    "MELI",
    "META",
    "MNST",
    "MPWR",
    "MRVL",
    "MSFT",
    "MSTR",
    "MU",
    "NBIS",
    "NFLX",
    "NVDA",
    "NXPI",
    "ODFL",
    "ORLY",
    "PANW",
    "PAYX",
    "PCAR",
    "PDD",
    "PEP",
    "PLTR",
    "PYPL",
    "QCOM",
    "REGN",
    "RKLB",
    "ROP",
    "ROST",
    "SBUX",
    "SHOP",
    "SNPS",
    "SPCX",
    "STX",
    "TEAM",
    "TER",
    "TMUS",
    "TRI",
    "TSLA",
    "TTWO",
    "TXN",
    "VRTX",
    "WBD",
    "WDAY",
    "WDC",
    "WMT",
    "XEL",
]

# Current-plan discovery watchlist only. This is not historical index membership
# and must never replace the point-in-time universe used by backtests.
ADDITIONAL_LIQUID_US_TICKERS = [
    "ABBV", "BAC", "BA", "BRK-B", "CAT", "C", "CVX", "DE", "DIS",
    "DUK", "EA", "GE", "GM", "GS", "HD", "IBM", "JNJ", "JPM",
    "KO", "LLY", "LMT", "LOW", "MA", "MCD", "MRK", "NEE", "NKE",
    "PFE", "PG", "RTX", "SO", "T", "TGT", "UNH", "UPS", "V",
    "VZ", "WFC", "WM", "XOM",
]

def expanded_current_scanner_universe(*, as_of=None):
    """Resolve live discovery membership at call time, not process import time."""
    return current_scanner_symbols(
        NASDAQ_100_TEST_UNIVERSE + ADDITIONAL_LIQUID_US_TICKERS, as_of=as_of
    )


EXPANDED_CURRENT_SCANNER_UNIVERSE = expanded_current_scanner_universe()

# Optional wider current-day discovery pool. These are candidate symbols, not
# competition eligibility assertions or historical constituent snapshots.
# Provider quote/date checks still decide whether an idea can enter a plan.
BROAD_RESEARCH_SECTORS = {
    "financials": ("AIG", "ALL", "APO", "AXP", "BLK", "BX", "CB", "CME", "ICE", "KKR", "MET", "MRSH", "PNC", "PGR", "PRU", "SCHW", "SPGI", "TRV", "USB"),
    "healthcare": ("ABT", "BMY", "BSX", "CI", "CVS", "DHR", "HCA", "HUM", "MDT", "SYK", "TMO", "ZTS"),
    "industrials": ("ACN", "DAL", "EMR", "ETN", "FDX", "GD", "GEV", "NOC", "PH", "ROK", "UAL", "UNP"),
    "energy_materials": ("APD", "COP", "CRH", "ECL", "EOG", "FCX", "KMI", "NEM", "NUE", "OXY", "PSX", "SHW", "SLB", "STLD", "VLO"),
    "consumer": ("CMG", "CPB", "DPZ", "F", "GIS", "KMB", "KR", "MO", "RCL", "SYY", "TJX"),
    "real_estate_utilities": ("AMT", "EQIX", "O", "PLD", "PSA", "SPG", "WY"),
    "technology_communications": ("CRM", "DELL", "HPE", "HPQ", "NOW", "ORCL", "PINS", "SNOW", "UBER"),
}

def broad_current_scanner_universe(*, as_of=None):
    """Refresh live candidates while leaving historical universes untouched."""
    return current_scanner_symbols(
        NASDAQ_100_TEST_UNIVERSE
        + ADDITIONAL_LIQUID_US_TICKERS
        + [ticker for group in BROAD_RESEARCH_SECTORS.values() for ticker in group],
        as_of=as_of,
    )


BROAD_CURRENT_SCANNER_UNIVERSE = broad_current_scanner_universe()
