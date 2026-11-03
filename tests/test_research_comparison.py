import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import json

import httpx
import pytest

from api import research_comparison as comparison, research_news as news, external_research as sec

NOW = datetime(2026, 11, 3, 14, tzinfo=timezone.utc)

def fact(year, value, **kw):
    return {"start": f"{year}-01-01", "end": f"{year}-12-31", "filed": f"{year+1}-02-01",
            "form": "10-K", "val": value, "accn": f"0000000001-{year%100:02d}-000001", **kw}

def facts(current=110, previous=100):
    return {"cik": 1, "facts": {"us-gaap": {"Revenues": {"units": {"USD": [fact(2024, previous), fact(2025, current)]}}}}}

def opinion(signals=("BUY", "WAIT")):
    return {"ticker": "AAPL", "price_as_of": "2026-11-02", "strategies": [{"strategy": f"Strategy {i}", "signal": s} for i,s in enumerate(signals)]}

def test_financial_comparisons_cite_sources_and_preserve_inputs():
    data=facts(); original=deepcopy(data)
    evidence=comparison.financial_evidence(data,NOW)
    assert data==original and len(evidence)==1
    row=evidence[0]
    assert row["direction"]=="POSITIVE" and "10.0%" in row["reason"]
    assert row["current_value"]==110 and row["prior_value"]==100
    assert row["url"].startswith("https://www.sec.gov/Archives/") and row["prior_url"] != row["url"]

@pytest.mark.parametrize("mutate", [
    lambda rows: rows[-1].update(filed="2026-12-01"),
    lambda rows: rows[-1].update(end="2026-12-31"),
    lambda rows: rows[-1].update(start="2025-10-01"),
    lambda rows: rows[-1].update(val=float("nan")),
    lambda rows: rows[-1].update(accn="../../secret"),
    lambda rows: rows[-1].update(form="10-Q"),
])
def test_unusable_or_future_financials_never_become_directional(mutate):
    data=facts(); mutate(data["facts"]["us-gaap"]["Revenues"]["units"]["USD"])
    assert comparison.financial_evidence(data,NOW)==[]

def test_latest_revision_used_but_not_a_future_revision():
    data=facts(); rows=data["facts"]["us-gaap"]["Revenues"]["units"]["USD"]
    rows += [fact(2025,80,filed="2026-04-01"),fact(2025,500,filed="2026-12-01")]
    assert comparison.financial_evidence(data,NOW)[0]["direction"]=="NEGATIVE"

def test_negative_prior_profit_not_reported_as_growth():
    data=facts(10,-10); data["facts"]["us-gaap"]["NetIncomeLoss"]=data["facts"]["us-gaap"].pop("Revenues")
    row=comparison.financial_evidence(data,NOW)[0]
    assert row["direction"]=="NEUTRAL" and "No percentage" in row["reason"]

@pytest.mark.parametrize("value,signals,expected", [(110,("BUY","WAIT"),"SUPPORTS"),(80,("BUY","WAIT"),"CONFLICTS"),(110,("BUY","AVOID"),"MIXED"),(80,("AVOID","WAIT"),"SUPPORTS"),(110,("WAIT",),"INCONCLUSIVE")])
def test_alignment_uses_actual_signals_without_changing_them(value,signals,expected):
    source=opinion(signals); original=deepcopy(source)
    result=comparison.compare_research(source,comparison.financial_evidence(facts(value),NOW),[],NOW)
    assert result["assessment"]==expected and source==original

def article(**kw):
    return {"id":"1","tickers":["AAPL"],"title":"Company results","article_url":"https://example.com/report",
            "published_utc":"2026-11-03T12:00:00Z","publisher":{"name":"Example"},
            "insights":[{"ticker":"AAPL","sentiment":"negative","sentiment_reasoning":"Provider reports weaker guidance."}],**kw}

def test_news_is_ticker_specific_dated_deduplicated_and_attributed():
    source=article(); output=news.parse_news({"results":[source,source]},"AAPL",NOW)
    assert len(output)==1 and output[0]["direction"]=="NEGATIVE"
    assert "Provider-generated" in output[0]["basis"]
    context=comparison.compare_research(opinion(),output,[],NOW)
    assert context["assessment"]=="CONFLICTS" and context["evidence"][0]["newer_than_price_date"]
    other=news.parse_news({"results":[article(insights=[{"ticker":"MSFT","sentiment":"positive"}])]},"AAPL",NOW)
    assert other[0]["direction"]=="NEUTRAL"

@pytest.mark.parametrize("change", [{"published_utc":"2026-11-04T00:00:00Z"},{"published_utc":"2026-01-01T00:00:00Z"},{"article_url":"javascript:alert(1)"},{"article_url":"https://secret@example.com"},{"tickers":["MSFT"]}])
def test_news_rejects_stale_future_unsafe_or_unrelated_items(change):
    assert news.parse_news({"results":[article(**change)]},"AAPL",NOW)==[]

def test_news_disabled_requires_no_network(monkeypatch):
    monkeypatch.delenv("STOCK_COMP_MASSIVE_NEWS_ENABLED",raising=False)
    monkeypatch.setattr(news.httpx,"AsyncClient",lambda **kw:pytest.fail("Unexpected network"))
    items,provider=asyncio.run(news.load_news("AAPL",NOW))
    assert not items and provider["status"]=="unavailable"

def test_source_failure_keeps_technical_opinion(monkeypatch):
    async def fail(*args): raise ValueError("PRIVATE_KEY")
    monkeypatch.setattr(comparison,"load_external_context",fail)
    source=opinion()
    result=asyncio.run(comparison.enrich_opinion("AAPL",lambda ticker:source))
    assert result["strategies"]==source["strategies"]
    assert result["external_context"]["assessment"]=="INCONCLUSIVE"
    assert "PRIVATE_KEY" not in json.dumps(result)

def test_neutral_filings_do_not_validate_buys():
    evidence=[{"id":"filing", "url":"https://www.sec.gov/example", "published_at":"2026-11-03", "direction":"NEUTRAL"}]
    assert comparison.compare_research(opinion(),evidence,[],NOW)["assessment"]=="INCONCLUSIVE"

def test_news_transport_fixed_host_no_key_in_url_cached_and_bounded(monkeypatch):
    real_client=httpx.AsyncClient; calls=[]
    for name,value in {"STOCK_COMP_MASSIVE_NEWS_ENABLED":"true","STOCK_COMP_MASSIVE_NEWS_DISPLAY_AUTHORIZED":"true","STOCK_COMP_MASSIVE_API_KEY":"private-test-key"}.items(): monkeypatch.setenv(name,value)
    news._cache.clear();monkeypatch.setattr(news,"_next_request",0)
    def handle(request):
        calls.append(request)
        assert request.url.host=="api.massive.com" and "private-test-key" not in str(request.url)
        assert request.headers["authorization"]=="Bearer private-test-key"
        return httpx.Response(200,json={"results":[article()],"next_url":"https://evil.example/steal"})
    def client(**kw):
        assert kw["follow_redirects"] is False and kw["trust_env"] is False
        return real_client(transport=httpx.MockTransport(handle),**kw)
    monkeypatch.setattr(news.httpx,"AsyncClient",client)
    first=asyncio.run(news.load_news("AAPL",NOW)); second=asyncio.run(news.load_news("AAPL",NOW))
    assert first==second and len(calls)==1
    assert "private-test-key" not in json.dumps(first)
