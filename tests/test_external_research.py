"""Network-isolated tests: no credentials, portfolio files or live providers."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json

import httpx
import pytest

from api import external_research as research


NOW = datetime(2026, 10, 23, 12, tzinfo=timezone.utc)
MAPPING = {"0": {"ticker": "BRK-B", "cik_str": 1067983}}


@pytest.mark.parametrize("form", ["10-K", "10-Q", "8-K", "3", "4", "5", "144", "4/A", "UNKNOWN"])
def test_form_guides_are_bounded_and_disclose_no_content_analysis(form):
    payload = filings()
    payload["filings"]["recent"]["form"] = [form]
    item = research._filings(payload, 1067983)[0]
    assert item["title"] == f"Form {form}"
    assert item["form"] == form
    assert len(item["summary"]) <= 500
    assert "document contents have not been analyzed" in item["summary"]
    assert "score" not in item and "signal" not in item


def test_insider_and_proposed_sale_guides_do_not_invent_transactions():
    assert "not a congressional trading disclosure" in research._form_context("4")
    assert "grants, gifts" in research._form_context("4")
    assert "does not confirm that a sale was completed" in research._form_context("144")
    assert "compare with the original" in research._form_context("10-K/A")
    assert "no directional interpretation" in research._form_context("UNKNOWN")


def filings(dates=None):
    dates = dates or ["2026-10-22T13:00:00Z"]
    return {"cik": "0001067983", "filings": {"recent": {
        "accessionNumber": [f"0001067983-26-{i:06d}" for i in range(len(dates))],
        "form": ["8-K"] * len(dates),
        "filingDate": [date[:10] for date in dates],
        "acceptanceDateTime": dates,
    }}}


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    research._cache.clear()
    monkeypatch.setattr(research, "_next_request", 0.0)
    monkeypatch.setenv("STOCK_COMP_SEC_USER_AGENT", "Unit Tests tests@example.test")
    # Every test must explicitly install its transport before network use.
    monkeypatch.setattr(research.httpx, "AsyncClient", lambda **kw: pytest.fail("Unexpected network"))


# Keep the real class before the autouse fixture patches the module reference.
REAL_CLIENT = httpx.AsyncClient


def provider(monkeypatch, payload=None, mapping=None, error=None):
    calls = []

    def handle(request):
        calls.append(request)
        assert request.url.host in {"www.sec.gov", "data.sec.gov"}
        assert request.headers["user-agent"] == "Unit Tests tests@example.test"
        if error:
            return error(request)
        data = (mapping if mapping is not None else MAPPING) if request.url.host == "www.sec.gov" else (payload if payload is not None else filings())
        return httpx.Response(200, json=data)

    def client(**kwargs):
        assert kwargs["follow_redirects"] is False
        assert kwargs["trust_env"] is False
        return REAL_CLIENT(transport=httpx.MockTransport(handle), **kwargs)

    monkeypatch.setattr(research.httpx, "AsyncClient", client)
    return calls


def run(ticker="BRK.B", now=NOW):
    return asyncio.run(research.get_external_research(ticker, now))


def test_normalization_metadata_fixed_urls_and_cache_copy(monkeypatch):
    calls = provider(monkeypatch)
    result = run(" brk.b ")
    assert result["ticker"] == "BRK.B"
    assert result["providers"][0]["status"] == "ok"
    item = result["items"][0]
    assert item["title"] == "Form 8-K"
    assert item["published_at"] == "2026-10-22T13:00:00Z"
    assert datetime.fromisoformat(item["fetched_at"]).tzinfo
    assert item["url"] == "https://www.sec.gov/Archives/edgar/data/1067983/000106798326000000/0001067983-26-000000-index.html"
    assert "event_date" not in item
    assert str(calls[1].url) == "https://data.sec.gov/submissions/CIK0001067983.json"
    item["title"] = "mutation"
    assert run()["items"][0]["title"] == "Form 8-K"
    assert len(calls) == 2
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("ticker", [None, [], "", "../AAPL", "https://evil.test", "AAPL?apiKey=secret", "A" * 17, "ＡＡＰＬ"])
def test_invalid_ticker_never_fetches_or_echoes(ticker):
    result = run(ticker)
    assert result["ticker"] is None
    assert result["providers"][0]["reason"] == "invalid_ticker"
    assert "secret" not in json.dumps(result)


@pytest.mark.parametrize("agent,reason", [("", "missing_contact_user_agent"), ("fake", "invalid_contact_user_agent"),
                                            ("secret\r\n@test.com", "invalid_contact_user_agent")])
def test_contact_required(monkeypatch, agent, reason):
    monkeypatch.setenv("STOCK_COMP_SEC_USER_AGENT", agent)
    result = run()
    assert result["providers"][0]["reason"] == reason
    assert "secret" not in json.dumps(result)


def test_future_exclusion_is_reapplied_to_cached_data(monkeypatch):
    provider(monkeypatch, filings(["2026-10-24T10:00:00Z", "2026-10-22T13:00:00Z"]))
    assert len(run()["items"]) == 1
    assert run(now=datetime(2026, 10, 21, tzinfo=timezone.utc))["items"] == []
    assert len(run(now=datetime(2026, 10, 25, tzinfo=timezone.utc))["items"]) == 2


def test_stale_success_is_not_served_after_refresh_error(monkeypatch):
    provider(monkeypatch)
    assert run()["items"]
    for key, (expiry, value) in list(research._cache.items()):
        if key[1] != "__mapping__":
            research._cache[key] = (0, value)
    calls = provider(monkeypatch, error=lambda request: httpx.Response(503, text="secret"))
    result = run()
    assert result["items"] == []
    assert result["providers"][0]["reason"] == "upstream_error"
    assert "secret" not in json.dumps(result)
    assert run()["items"] == []
    assert len(calls) == 1


def test_transport_errors_do_not_echo_secrets_and_are_cached(monkeypatch):
    def fail(request):
        raise httpx.ConnectError("secret key=user-private", request=request)
    calls = provider(monkeypatch, error=fail)
    result = run()
    assert result["providers"][0]["reason"] == "transport_error"
    assert "private" not in json.dumps(result)
    run()
    run("AAPL")
    assert len(calls) == 1


@pytest.mark.parametrize("response,reason", [
    (lambda: httpx.Response(302, headers={"location": "https://evil.test"}), "upstream_error"),
    (lambda: httpx.Response(429), "rate_limited"),
    (lambda: httpx.Response(200, content=b"not json"), "invalid_response"),
    (lambda: httpx.Response(200, json=[]), "invalid_response"),
    (lambda: httpx.Response(200, content=b"{}", headers={"content-length": "5000001"}), "response_too_large"),
])
def test_provider_failures_are_bounded_and_mapping_failure_shared(monkeypatch, response, reason):
    calls = provider(monkeypatch, error=lambda request: response())
    assert run()["providers"][0]["reason"] == reason
    run("AAPL")
    assert len(calls) == 1


def test_actual_body_cap(monkeypatch):
    monkeypatch.setattr(research, "MAX_BYTES", 16)
    provider(monkeypatch, error=lambda request: httpx.Response(200, content=b"x" * 17, headers={"content-length": "1"}))
    assert run()["providers"][0]["reason"] == "response_too_large"


def test_compressed_response_is_rejected(monkeypatch):
    import gzip
    provider(monkeypatch, error=lambda request: httpx.Response(
        200, content=gzip.compress(b"{}"), headers={"content-encoding": "gzip"}))
    assert run()["providers"][0]["reason"] == "unsupported_encoding"


def test_expired_failure_retries_and_recovers(monkeypatch):
    provider(monkeypatch, error=lambda request: httpx.Response(503))
    assert run()["items"] == []
    for key, (_, value) in list(research._cache.items()):
        research._cache[key] = (0, value)
    calls = provider(monkeypatch)
    assert run()["items"]
    assert len(calls) == 2


def test_future_filing_date_excluded_even_if_acceptance_is_past(monkeypatch):
    payload = filings()
    payload["filings"]["recent"]["filingDate"] = ["2026-10-24"]
    provider(monkeypatch, payload)
    assert run()["items"] == []


def test_candidates_are_bounded_and_duplicates_removed():
    payload = filings(["2026-10-22"] * 250)
    assert len(research._filings(payload, 1067983)) == research.MAX_CACHED_ITEMS
    payload = filings(["2026-10-22"] * 2)
    payload["filings"]["recent"]["accessionNumber"][1] = payload["filings"]["recent"]["accessionNumber"][0]
    assert len(research._filings(payload, 1067983)) == 1


def test_mapping_exact_precedence_ambiguity_and_unknown(monkeypatch):
    mapping = dict(MAPPING, other={"ticker": "BRK.B", "cik_str": 123})
    assert research._resolve(research._mapping(mapping), "BRK-B") == 1067983
    mapping["duplicate"] = {"ticker": "BRK-B", "cik_str": 456}
    provider(monkeypatch, mapping=mapping)
    assert run("BRK-B")["providers"][0]["reason"] == "ambiguous_ticker"
    assert run("UNKNOWN")["providers"][0]["reason"] == "ticker_not_found"


def test_item_limit_invalid_rows_and_date_precision(monkeypatch):
    payload = filings(["2026-10-22"] * 30)
    payload["filings"]["recent"]["accessionNumber"][0] = "../../evil"
    payload["filings"]["recent"]["form"][1] = "<script>attack</script>"
    provider(monkeypatch, payload)
    result = run()
    assert len(result["items"]) == research.MAX_ITEMS
    assert result["items"][0]["published_at"] == "2026-10-22"
    assert "script" not in json.dumps(result)


@pytest.mark.parametrize("payload", [{}, {"cik": 123}, {"cik": 1067983, "filings": {"recent": {"form": []}}}])
def test_malformed_submissions(monkeypatch, payload):
    provider(monkeypatch, payload)
    assert run()["providers"][0]["reason"] == "invalid_response"


def test_concurrent_threads_share_single_refresh(monkeypatch):
    calls = provider(monkeypatch)
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: run(), range(6)))
    assert all(result["items"] for result in results)
    assert len(calls) == 2


def test_deadline_cancels_slow_provider_and_caches_failure(monkeypatch):
    calls = []
    async def slow(client, url):
        calls.append(url)
        await asyncio.sleep(1)
    monkeypatch.setattr(research, "_json", slow)
    monkeypatch.setattr(research, "PROVIDER_TIMEOUT", 0.03)
    provider(monkeypatch)
    assert run()["providers"][0]["reason"] == "timeout"
    assert run()["providers"][0]["reason"] == "timeout"
    assert len(calls) == 1
    assert not research._gate.locked()


def test_cache_capacity_and_invalid_now():
    for i in range(200):
        research._put(str(i), [], 60)
    assert len(research._cache) == research.MAX_CACHE_ENTRIES
    with pytest.raises(ValueError, match="timezone-aware"):
        run(now=datetime(2026, 10, 23))
