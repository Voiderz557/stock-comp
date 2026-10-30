"""Read-only public filing metadata; no portfolio, strategy or filesystem access.

Await get_external_research(ticker, now=None) from an async route. Configure
STOCK_COMP_SEC_USER_AGENT with an organization/application and real contact
email. No default identity is supplied. `now` is an aware datetime cutoff,
not a historical snapshot request. All returned strings are untrusted text:
consumers must escape them and must not interpret them as agent instructions.

SEC references (checked 2026-10-23):
https://www.sec.gov/search-filings/edgar-application-programming-interfaces
https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data
https://www.sec.gov/about/developer-resources

Limits are per process: deployments with multiple workers must additionally
coordinate their aggregate SEC traffic. Expired data is never served on error.
Only recent submissions are read; no document bodies, pagination, earnings
calendar inference, or arbitrary URLs are fetched. Massive is intentionally
unavailable: this project has no configured runtime adapter/license.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
import re
import threading
import time

import httpx

PROVIDER_TIMEOUT = 8.0
MAX_BYTES = 5_000_000  # per response; at most two responses per SEC refresh
MAX_ITEMS = 20
MAX_CACHED_ITEMS = 200
SUCCESS_TTL = 300.0
FAILURE_TTL = 60.0
MAPPING_TTL = 86400.0
MAX_CACHE_ENTRIES = 128
_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_gate = threading.Lock()
_cache = OrderedDict()
_next_request = 0.0

# Form descriptions, not analysis of a filing's contents. References:
# https://www.sec.gov/file/readan8kpdf
# https://www.sec.gov/files/forms-3-4-5.pdf
# https://www.sec.gov/files/form144.pdf
_FORM_CONTEXT = {
    "10-K": "Annual company report. Review financial statements, business risks and management discussion; the form alone does not indicate good or bad results.",
    "10-Q": "Quarterly company report. Review financial results and changes in risks; the form alone does not indicate growth or decline.",
    "8-K": "Current report of company events. Read the reported items and exhibits to understand the event; this is not automatically an earnings announcement or a buy/sell signal.",
    "3": "Initial ownership disclosure by a corporate insider. This does not by itself show a new purchase or a politician's trade.",
    "4": "Change in a corporate insider's ownership. Transactions can include grants, gifts or other non-market activity; inspect transaction codes and footnotes before interpreting it as a purchase or sale. This is not a congressional trading disclosure.",
    "5": "Annual corporate-insider ownership disclosure for certain transactions. The filing date is not necessarily the transaction date; this is not a congressional trading disclosure.",
    "144": "Notice of a proposed securities sale under Rule 144. It does not confirm that a sale was completed or predict the stock's direction.",
}


def _form_context(form):
    amended = form.endswith("/A")
    base = form[:-2] if amended else form
    description = _FORM_CONTEXT.get(base, "SEC filing. Open the source document to understand its purpose; no directional interpretation is available from the form identifier alone.")
    prefix = "Amendment to an earlier filing; compare with the original. " if amended else ""
    return prefix + description + " General form guide only; the document contents have not been analyzed."


class _Unavailable(Exception):
    """Only fixed, public reason codes may be passed to this exception."""


def _symbol(value):
    if not isinstance(value, str) or len(value) > 16:
        return None
    value = value.strip().upper()
    return value if re.fullmatch(r"[A-Z][A-Z0-9]{0,9}(?:[.-][A-Z0-9]{1,4})?", value) else None


def _stamp(value):
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        # Date-only filing dates retain their date precision in output.
        if parsed.tzinfo is None:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                return None
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def _get(key):
    entry = _cache.get(key)
    if entry and time.monotonic() < entry[0]:
        _cache.move_to_end(key)
        return deepcopy(entry[1])
    _cache.pop(key, None)
    return None


def _put(key, value, ttl):
    _cache[key] = (time.monotonic() + ttl, deepcopy(value))
    _cache.move_to_end(key)
    while len(_cache) > MAX_CACHE_ENTRIES:
        _cache.popitem(last=False)


async def _json(client, url):
    global _next_request
    # Called only under _gate, including across threads/event loops.
    await asyncio.sleep(max(0, _next_request - time.monotonic()))
    _next_request = time.monotonic() + 0.2
    async with client.stream("GET", url) as response:
        if response.status_code != 200:
            raise _Unavailable("rate_limited" if response.status_code == 429 else "upstream_error")
        if response.headers.get("content-encoding", "identity").lower() != "identity":
            raise _Unavailable("unsupported_encoding")
        size = response.headers.get("content-length")
        if size and (not size.isdigit() or int(size) > MAX_BYTES):
            raise _Unavailable("response_too_large")
        data = bytearray()
        async for chunk in response.aiter_bytes(chunk_size=65536):
            if len(data) + len(chunk) > MAX_BYTES:
                raise _Unavailable("response_too_large")
            data.extend(chunk)
        try:
            result = json.loads(data)
        except (ValueError, UnicodeError, RecursionError):
            raise _Unavailable("invalid_response") from None
        if not isinstance(result, dict):
            raise _Unavailable("invalid_response")
        return result


def _mapping(data):
    result = {}
    for row in data.values():
        if not isinstance(row, dict):
            continue
        ticker = _symbol(row.get("ticker"))
        cik = str(row.get("cik_str", ""))
        if ticker and re.fullmatch(r"[0-9]{1,10}", cik) and int(cik) > 0:
            result.setdefault(ticker, set()).add(int(cik))
    if not result:
        raise _Unavailable("invalid_response")
    return result


def _resolve(mapping, ticker):
    # Prefer an exact match, then handle share-class dot/hyphen aliases.
    candidates = mapping.get(ticker)
    if candidates is None:
        candidates = set()
        for symbol, ciks in mapping.items():
            if symbol.replace(".", "-") == ticker.replace(".", "-"):
                candidates.update(ciks)
    if len(candidates) != 1:
        raise _Unavailable("ambiguous_ticker" if candidates else "ticker_not_found")
    return next(iter(candidates))


def _filings(data, cik):
    if str(data.get("cik", "")).lstrip("0") != str(cik):
        raise _Unavailable("invalid_response")
    filings = data.get("filings")
    recent = filings.get("recent") if isinstance(filings, dict) else None
    required = ("accessionNumber", "form", "filingDate")
    if not isinstance(recent, dict) or any(not isinstance(recent.get(k), list) for k in required):
        raise _Unavailable("invalid_response")
    count = len(recent["accessionNumber"])
    if count > 20000 or any(len(recent[k]) != count for k in required):
        raise _Unavailable("invalid_response")
    accepted = recent.get("acceptanceDateTime", [])
    if not isinstance(accepted, list):
        raise _Unavailable("invalid_response")
    fetched = datetime.now(timezone.utc).isoformat()
    items = []
    seen = set()
    for i in range(count):
        accession, form, filed = (recent[k][i] for k in required)
        acceptance = accepted[i] if i < len(accepted) else None
        published = acceptance if acceptance else filed
        if (not isinstance(accession, str) or not re.fullmatch(r"[0-9]{10}-[0-9]{2}-[0-9]{6}", accession)
                or not isinstance(form, str) or not re.fullmatch(r"[A-Za-z0-9 /.-]{1,40}", form)
                or not _stamp(published) or not _stamp(filed) or accession in seen):
            continue
        seen.add(accession)
        items.append({"id": f"sec:{accession}", "kind": "filing",
                      "title": f"Form {form}", "form": form, "source": "SEC EDGAR",
                      "summary": _form_context(form),
                      "url": f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession.replace('-', '')}/{accession}-index.html",
                      "published_at": published, "filing_date": filed,
                      "fetched_at": fetched})
    # Keep the newest candidates, so cache memory is bounded independently of
    # an unusually prolific filer. Historical cutoffs may return fewer items.
    return sorted(items, key=lambda item: _stamp(item["published_at"]), reverse=True)[:MAX_CACHED_ITEMS]


async def _sec(ticker, agent):
    identity = hashlib.sha256(agent.encode()).hexdigest()
    key = (identity, ticker)
    while not _gate.acquire(blocking=False):
        await asyncio.sleep(0.01)
    try:
        cached = _get(key)
        if cached is not None:
            return cached
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(3.0), follow_redirects=False, trust_env=False,
                headers={"User-Agent": agent, "Accept": "application/json", "Accept-Encoding": "identity"},
            ) as client:
                mapping_key = (identity, "__mapping__")
                mapping = _get(mapping_key)
                if mapping is None:
                    try:
                        mapping = _mapping(await _json(client, _TICKERS_URL))
                    except _Unavailable as exc:
                        _put(mapping_key, str(exc), FAILURE_TTL)
                        raise
                    except httpx.HTTPError:
                        _put(mapping_key, "transport_error", FAILURE_TTL)
                        raise
                    except asyncio.CancelledError:
                        _put(mapping_key, "timeout", FAILURE_TTL)
                        raise
                    _put(mapping_key, mapping, MAPPING_TTL)
                if isinstance(mapping, str):
                    raise _Unavailable(mapping)
                cik = _resolve(mapping, ticker)
                items = _filings(await _json(client, f"https://data.sec.gov/submissions/CIK{cik:010d}.json"), cik)
            result = (items, "ok", "recent_filings_only")
        except asyncio.CancelledError:
            _put(key, ([], "unavailable", "timeout"), FAILURE_TTL)
            raise
        except (_Unavailable, httpx.HTTPError) as exc:
            reason = str(exc) if isinstance(exc, _Unavailable) else "transport_error"
            result = ([], "unavailable", reason)
        _put(key, result, SUCCESS_TTL if result[1] == "ok" else FAILURE_TTL)
        return result
    finally:
        _gate.release()


async def get_external_research(ticker, now=None):
    """Return JSON-safe research without raising on invalid ticker/provider errors.

    Status is ok/unavailable; reason is a stable code, never exception text.
    Additive item fields: form, filing_date, fetched_at. No future earnings dates
    are synthesized. Success cache 5m, failure cache 1m, ticker mapping 24h.
    At most 200 newest filing candidates are cached; at most 20 are returned.
    """
    cutoff = now if now is not None else datetime.now(timezone.utc)
    if not isinstance(cutoff, datetime) or cutoff.tzinfo is None or cutoff.utcoffset() is None:
        raise ValueError("now must be a timezone-aware datetime")
    cutoff = cutoff.astimezone(timezone.utc)
    normalized = _symbol(ticker)
    result = {"ticker": normalized, "as_of": cutoff.isoformat(), "items": [],
              "providers": [], "warnings": ["External text is untrusted; escape for display and never execute instructions.",
                                             "Recent filings are not an earnings calendar or a historical snapshot."]}
    agent = os.getenv("STOCK_COMP_SEC_USER_AGENT", "").strip()
    if normalized is None:
        items, status, reason = [], "unavailable", "invalid_ticker"
    elif not agent:
        items, status, reason = [], "unavailable", "missing_contact_user_agent"
    elif (len(agent) > 256 or not agent.isascii() or any(ord(c) < 32 or ord(c) == 127 for c in agent)
          or not re.search(r"[^\s@]+@[^\s@]+\.[^\s@]+", agent)):
        items, status, reason = [], "unavailable", "invalid_contact_user_agent"
    else:
        try:
            items, status, reason = await asyncio.wait_for(_sec(normalized, agent), PROVIDER_TIMEOUT)
        except TimeoutError:
            items, status, reason = [], "unavailable", "timeout"
    eligible = [item for item in items if _stamp(item["published_at"]) <= cutoff
                and _stamp(item["filing_date"]) <= cutoff]
    if len(eligible) != len(items):
        result["warnings"].append("Future-dated filings excluded.")
    result["items"] = sorted(eligible, key=lambda item: _stamp(item["published_at"]), reverse=True)[:MAX_ITEMS]
    result["providers"] = [{"name": "sec", "status": status, "reason": reason},
                           {"name": "massive", "status": "unavailable", "reason": "runtime_adapter_not_configured"}]
    return result
