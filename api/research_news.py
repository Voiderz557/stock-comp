"""Optional licensed news adapter. Disabled unless explicitly configured server-side."""
import asyncio
from collections import OrderedDict
from copy import deepcopy
from datetime import timedelta
import hashlib
import json
import os
import threading
import time

import httpx

from api.external_research import _symbol, _stamp

_gate = threading.Lock()
_cache = OrderedDict()
_next_request = 0.0
MAX_BYTES = 1_000_000
NEWS_LIMIT = 10
SUCCESS_TTL = 300
FAILURE_TTL = 60


def parse_news(data, ticker, now):
    from api.research_comparison import safe_source_url, MAX_NEWS_AGE_DAYS
    results = data.get("results")
    if not isinstance(results, list):
        raise ValueError("invalid_response")
    evidence, seen = [], set()
    for item in results[:NEWS_LIMIT]:
        if not isinstance(item, dict):
            continue
        url, published = item.get("article_url"), _stamp(item.get("published_utc"))
        if (not safe_source_url(url) or url in seen or not published or
            not now - timedelta(days=MAX_NEWS_AGE_DAYS) <= published <= now or
            ticker not in (item.get("tickers") or []) or not isinstance(item.get("title"), str)):
            continue
        seen.add(url)
        # Only the requested ticker's explicit provider insight is relevant.
        insights = [x for x in item.get("insights", []) if isinstance(x, dict) and x.get("ticker") == ticker]
        insight = insights[0] if len(insights) == 1 else {}
        sentiment = insight.get("sentiment")
        reasoning = insight.get("sentiment_reasoning")
        direction = {"positive": "POSITIVE", "negative": "NEGATIVE"}.get(sentiment, "NEUTRAL") if isinstance(reasoning, str) and reasoning.strip() else "NEUTRAL"
        publisher = item.get("publisher") or {}
        name = publisher.get("name") if isinstance(publisher, dict) else None
        evidence.append({"id": f"news:{str(item.get('id', ''))[:100]}",
            "source": f"{name[:80] if isinstance(name, str) else 'Unknown publisher'} via Massive",
            "title": item["title"][:200], "url": url, "published_at": published.isoformat(),
            "direction": direction, "basis": "Provider-generated news sentiment; article not independently verified",
            "reason": reasoning[:400] if direction != "NEUTRAL" else "No usable ticker-specific directional assessment. A headline alone is not enough to establish support or conflict."})
    return evidence


async def load_news(ticker, now):
    key = os.getenv("STOCK_COMP_MASSIVE_API_KEY", "").strip()
    # Explicit acknowledgement must reflect permission to show this data in this app.
    enabled = os.getenv("STOCK_COMP_MASSIVE_NEWS_ENABLED") == "true"
    licensed = os.getenv("STOCK_COMP_MASSIVE_NEWS_DISPLAY_AUTHORIZED") == "true"
    unavailable = lambda description: ([], {"name": "News", "status": "unavailable", "description": description})
    if not enabled or not licensed or not key:
        return unavailable("News was not checked. A server-side feed key and permission to display news in this app are not configured; no paid plan was enabled.")
    if not _symbol(ticker):
        return unavailable("Invalid ticker; no news request made.")
    if not _gate.acquire(blocking=False):
        return unavailable("News provider is busy. No news confirmation is available.")
    global _next_request
    cache_key = (hashlib.sha256(key.encode()).hexdigest(), ticker)
    try:
        cached = _cache.get(cache_key)
        if cached and time.monotonic() < cached[0]:
            data, status = deepcopy(cached[1])
        elif time.monotonic() < _next_request:
            return unavailable("News request limit reached; try again later. No paid fallback is used.")
        else:
            _next_request = time.monotonic() + 12.1
            try:
                data = await asyncio.wait_for(_fetch(ticker, key, now), timeout=5)
                status = "ok"
            except (httpx.HTTPError, TimeoutError, ValueError):
                data, status = {}, "unavailable"
            _cache[cache_key] = (time.monotonic() + (SUCCESS_TTL if status == "ok" else FAILURE_TTL), (data, status))
            _cache.move_to_end(cache_key)
            while len(_cache) > 128:
                _cache.popitem(last=False)
        if status != "ok":
            return unavailable("News provider could not be checked. Authentication, entitlement, rate limits or network availability may need attention.")
        evidence = parse_news(data, ticker, now)
        return evidence, {"name": "News", "status": "ok", "description": f"Checked up to {NEWS_LIMIT} articles from the last seven days; {len(evidence)} usable items. Provider sentiment is an interpretation, not a fact. Coverage is not exhaustive."}
    except (ValueError, TypeError, KeyError):
        return unavailable("News provider returned unusable data; no directional inference was made.")
    finally:
        _gate.release()


async def _fetch(ticker, key, now):
    async with httpx.AsyncClient(timeout=3, follow_redirects=False, trust_env=False) as client:
        async with client.stream("GET", "https://api.massive.com/v2/reference/news",
            params={"ticker": ticker, "limit": NEWS_LIMIT, "sort": "published_utc", "order": "desc",
                    "published_utc.gte": (now - timedelta(days=7)).isoformat(), "published_utc.lte": now.isoformat()},
            headers={"Authorization": f"Bearer {key}", "Accept-Encoding": "identity"}) as response:
            if response.status_code != 200 or response.headers.get("content-encoding", "identity") != "identity":
                raise ValueError("provider_unavailable")
            body = bytearray()
            async for chunk in response.aiter_bytes(chunk_size=65536):
                if len(body) + len(chunk) > MAX_BYTES:
                    raise ValueError("response_too_large")
                body.extend(chunk)
            data = json.loads(body)
            if not isinstance(data, dict):
                raise ValueError("invalid_response")
            # next_url is deliberately ignored: no arbitrary fetch or pagination.
            return data
