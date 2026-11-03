"""Cited, deterministic external context. Never modifies strategy output or trades."""
from datetime import datetime, timezone, timedelta
import asyncio
import math
import re
from urllib.parse import urlsplit

from api.external_research import get_external_research, get_company_facts, cached_company_profile, _stamp, _symbol

MATERIAL_CHANGE = 0.05
MAX_ANNUAL_AGE_DAYS = 550
MAX_NEWS_AGE_DAYS = 7
MAX_EVIDENCE = 17
ANNUAL_MIN_DAYS = 330
ANNUAL_MAX_DAYS = 400
METRICS = (
    ("Revenue", ("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet")),
    ("Net income", ("NetIncomeLoss",)),
)


def safe_source_url(value):
    if not isinstance(value, str) or len(value) > 2048 or re.search(r"[\s\\]", value):
        return False
    try:
        parsed = urlsplit(value)
        return parsed.scheme == "https" and bool(parsed.hostname) and not parsed.username and not parsed.password
    except ValueError:
        return False


def financial_evidence(data, as_of):
    """Compare like-duration annual GAAP/USD facts under one concept; no quarter/YTD mixing."""
    if not isinstance(data, dict) or not str(data.get("cik", "")).isdigit():
        return []
    cik = int(data["cik"])
    concepts = (data.get("facts") or {}).get("us-gaap") or {}
    evidence = []
    for label, tags in METRICS:
        for tag in tags:
            units = ((concepts.get(tag) or {}).get("units") or {}).get("USD") or []
            annual = {}
            for fact in units:
                if not isinstance(fact, dict):
                    continue
                start, end, filed = (_stamp(fact.get(key)) for key in ("start", "end", "filed"))
                val, accession = fact.get("val"), fact.get("accn")
                if (not start or not end or not filed or filed > as_of or end > as_of or
                    not ANNUAL_MIN_DAYS <= (end - start).days <= ANNUAL_MAX_DAYS or
                    fact.get("form") not in {"10-K", "10-K/A"} or
                    not isinstance(val, (int, float)) or isinstance(val, bool) or not math.isfinite(val) or
                    not isinstance(accession, str) or not re.fullmatch(r"\d{10}-\d{2}-\d{6}", accession)):
                    continue
                # Latest available revision of each identical annual period.
                key = (start, end)
                if key not in annual or filed > annual[key][0]:
                    annual[key] = (filed, fact)
            ordered = sorted(annual, key=lambda key: key[1], reverse=True)
            if not ordered:
                continue
            current_key = ordered[0]
            if (as_of - current_key[1]).days > MAX_ANNUAL_AGE_DAYS:
                continue
            prior_key = next((key for key in ordered[1:] if
                ANNUAL_MIN_DAYS <= (current_key[1] - key[1]).days <= ANNUAL_MAX_DAYS and
                abs((current_key[1] - current_key[0]).days - (key[1] - key[0]).days) <= 10), None)
            if prior_key is None:
                continue
            current, previous = annual[current_key][1], annual[prior_key][1]
            a, b = current["val"], previous["val"]
            direction = "NEUTRAL"
            if label == "Net income" and b <= 0:
                # Percent growth with a zero/negative denominator is misleading.
                explanation = f"{label} was ${a:,.0f}, compared with ${b:,.0f} in the prior annual period. No percentage-growth interpretation is assigned because the prior value was zero or negative."
            elif b <= 0:
                continue
            else:
                change = a / b - 1
                direction = "POSITIVE" if change >= MATERIAL_CHANGE else "NEGATIVE" if change <= -MATERIAL_CHANGE else "NEUTRAL"
                explanation = f"Reported annual {label.lower()} {'rose' if change >= 0 else 'fell'} {abs(change) * 100:.1f}% versus the comparable prior annual period (${a:,.0f} versus ${b:,.0f})."
            accession = current["accn"]
            prior_accession = previous["accn"]
            url = lambda acc: f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}/{acc}-index.html"
            evidence.append({"id": f"sec-fact:{tag}:{accession}", "source": "SEC EDGAR XBRL",
                "title": f"{label}: annual comparison", "url": url(accession), "prior_url": url(prior_accession),
                "published_at": max(current["filed"], previous["filed"]), "current_filed_at": current["filed"],
                "prior_filed_at": previous["filed"], "period_end": current["end"], "prior_period_end": previous["end"],
                "direction": direction, "basis": "Reported financial fact; rule-based interpretation, not SEC's investment opinion",
                "reason": explanation + " Annual results are backward-looking; expectations, valuation and one-off effects have not been assessed.",
                "metric": tag, "unit": "USD", "current_value": a, "prior_value": b})
            break
    return evidence


def compare_research(opinion, evidence, providers, as_of):
    """Attribute directional evidence to actual BUY/AVOID votes; WAIT has no direction."""
    buys = [r["strategy"] for r in opinion.get("strategies", []) if r.get("signal") == "BUY"]
    avoids = [r["strategy"] for r in opinion.get("strategies", []) if r.get("signal") == "AVOID"]
    output = []
    for item in evidence[:MAX_EVIDENCE]:
        published = _stamp(item.get("published_at"))
        if not published or published > as_of or not safe_source_url(item.get("url")):
            continue
        direction = item.get("direction", "NEUTRAL")
        supports = buys if direction == "POSITIVE" else avoids if direction == "NEGATIVE" else []
        challenges = avoids if direction == "POSITIVE" else buys if direction == "NEGATIVE" else []
        relationship = "MIXED" if supports and challenges else "SUPPORTS" if supports else "CONFLICTS" if challenges else "INCONCLUSIVE"
        price_date = opinion.get("price_as_of")
        after_close_date = bool(price_date and published.date().isoformat() > price_date)
        output.append({**item, "relationship": relationship, "supports_strategies": supports,
                       "challenges_strategies": challenges, "newer_than_price_date": after_close_date})
    support = any(item["supports_strategies"] for item in output)
    conflict = any(item["challenges_strategies"] for item in output)
    assessment = "MIXED" if support and conflict else "SUPPORTS" if support else "CONFLICTS" if conflict else "INCONCLUSIVE"
    descriptions = {
        "SUPPORTS": "Some outside evidence is consistent with the directional algorithm signals. It does not establish that the stock will rise or that buying is allowed.",
        "CONFLICTS": "Some outside evidence challenges the directional algorithm signals. Review the concerns before relying on those signals.",
        "MIXED": "Outside evidence is mixed: some supports directional signals and some challenges them. Read the individual sources rather than treating this as a combined BUY score.",
        "INCONCLUSIVE": "The available outside evidence does not establish support or conflict with the algorithms. Missing sources and routine filings are not positive confirmation.",
    }
    return {"as_of": as_of.isoformat(), "assessment": assessment, "summary": descriptions[assessment],
            "providers": providers, "evidence": output,
            "limitations": ["Technical signals remain unchanged. WAIT is not a bullish or bearish prediction.",
                "Annual financial comparisons are historical, not an earnings-surprise or valuation model; a 5% change is a descriptive threshold, not a tested trading rule.",
                "News sentiment, when available, is the data provider's interpretation, not a verified fact or this app's AI analysis.",
                "Only the displayed, bounded sources were checked. Missing or stale coverage prevents a complete conclusion. Market and portfolio restrictions still apply."]}


async def load_external_context(ticker, now):
    """Partial provider failures never erase valid technical evidence."""
    providers, evidence = [], []
    try:
        filings = await asyncio.wait_for(get_external_research(ticker, now=now), timeout=9)
        sec = next((p for p in filings["providers"] if p["name"] == "sec"), {})
        providers.append({"name": "SEC filings", "status": sec.get("status", "unavailable"),
                          "description": "Recent filing notices; document contents are not sentiment analysis."})
        for item in filings["items"][:5]:
            evidence.append({"id": item["id"], "source": item["source"], "title": item["title"],
                "url": item["url"], "published_at": item["published_at"], "direction": "NEUTRAL",
                "basis": "Filing notice only", "reason": item["summary"]})
        facts, status = await asyncio.wait_for(get_company_facts(ticker), timeout=7)
        financial = financial_evidence(facts, now)
        providers.append({"name": "SEC financial facts", "status": "ok" if financial else "unavailable",
                          "description": "Comparable annual GAAP/USD facts checked." if financial else "No usable comparable annual facts; unavailable is not neutral or positive evidence."})
        evidence = financial + evidence
    except (TimeoutError, ValueError, KeyError, TypeError):
        providers.append({"name": "SEC financial research", "status": "unavailable", "description": "Source check could not complete; technical signals are shown separately."})
    from api.research_news import load_news
    news, provider = await load_news(ticker, now)
    evidence += news
    providers.append(provider)
    return evidence, providers


async def enrich_opinion(ticker, opinion_loader):
    ticker = _symbol(ticker)
    if not ticker:
        raise ValueError("Invalid ticker")
    now = datetime.now(timezone.utc)
    deadline = asyncio.get_running_loop().time() + 50
    outside = asyncio.create_task(load_external_context(ticker, now))
    try:
        opinion = await asyncio.wait_for(asyncio.to_thread(opinion_loader, ticker), timeout=45)
        try:
            evidence, providers = await asyncio.wait_for(outside, timeout=max(0.01, deadline - asyncio.get_running_loop().time()))
        except Exception:
            evidence, providers = [], [{"name": "Outside research", "status": "unavailable", "description": "Source check failed or timed out; no outside confirmation is available."}]
        return {**opinion, "company": cached_company_profile(ticker), "external_context": compare_research(opinion, evidence, providers, now)}
    finally:
        if not outside.done():
            outside.cancel()
        await asyncio.gather(outside, return_exceptions=True)
