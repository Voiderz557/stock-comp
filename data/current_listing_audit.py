"""Read-only audit of current US listings against Nasdaq Trader directories.

This is *not* historical index membership and never edits a scanner list.
The directories are exchange snapshots; a missing symbol requires manual
investigation and is not proof of permanent provider unavailability.
"""

from __future__ import annotations

import json
from urllib.request import urlopen

from data.scanner_universe import BROAD_CURRENT_SCANNER_UNIVERSE


NASDAQ_DIRECTORY = "https://www.nasdaqtrader.com/dynamic/symdir/nasdaqlisted.txt"
OTHER_DIRECTORY = "https://www.nasdaqtrader.com/dynamic/symdir/otherlisted.txt"
EXCHANGE_SYMBOL_ALIASES = {"BRK-B": "BRK.B"}  # Yahoo notation vs exchange notation.


def _rows(text, symbol_field):
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        raise ValueError("Exchange directory is empty.")
    headers = lines[0].split("|")
    if symbol_field not in headers or "Test Issue" not in headers:
        raise ValueError("Exchange directory has unexpected columns.")
    return {
        fields[headers.index(symbol_field)]: dict(zip(headers, fields))
        for line in lines[1:]
        if (fields := line.split("|")) and len(fields) == len(headers)
        and fields[headers.index(symbol_field)]
    }


def audit_current_listings(symbols, nasdaq_text, other_text):
    """Compare provider symbols with two current exchange directories."""
    nasdaq = _rows(nasdaq_text, "Symbol")
    other = _rows(other_text, "ACT Symbol")
    findings = []
    for provider_symbol in dict.fromkeys(symbols):
        exchange_symbol = EXCHANGE_SYMBOL_ALIASES.get(provider_symbol, provider_symbol)
        row = nasdaq.get(exchange_symbol) or other.get(exchange_symbol)
        findings.append({
            "provider_symbol": provider_symbol,
            "exchange_symbol": exchange_symbol,
            "status": "LISTED" if row and row.get("Test Issue") == "N" else "UNVERIFIED",
            "exchange": "NASDAQ" if exchange_symbol in nasdaq else "OTHER" if exchange_symbol in other else None,
            "reason": None if row and row.get("Test Issue") == "N" else "No matching non-test listing; investigate, do not infer delisting.",
        })
    return findings


def fetch_current_listing_directories(timeout_seconds=10):
    """Fetch official snapshots only when this explicit audit is invoked."""
    with urlopen(NASDAQ_DIRECTORY, timeout=timeout_seconds) as response:
        nasdaq_text = response.read().decode("utf-8-sig")
    with urlopen(OTHER_DIRECTORY, timeout=timeout_seconds) as response:
        other_text = response.read().decode("utf-8-sig")
    return nasdaq_text, other_text


if __name__ == "__main__":
    print(json.dumps(audit_current_listings(BROAD_CURRENT_SCANNER_UNIVERSE, *fetch_current_listing_directories()), indent=2))
