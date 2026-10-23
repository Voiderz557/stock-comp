"""Explicit journal refresh using cached market data, never cached user data.

Route integration: await load_journal(await load_plan_history(access_token)).
Call only on explicit action. Authentication/account reads belong to the route.
One market load per process is allowed; busy requests return immediately. A
timeout or disconnected caller does not free capacity while its thread runs.
The monotonic deadline stops the sequential loader before its next ticker.
It cannot interrupt cache-lock acquisition or the current ticker's provider
calls/retries: those still depend on the existing loader/provider timeouts.
Only public symbols/date bounds enter the worker; account histories and journal
results are request-local. No portfolio store or account writes are involved.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import BoundedSemaphore
from time import monotonic

from api.recommendation_journal import build_recommendation_journal
from data.market_data import load_market_data

PRICE_LOAD_TIMEOUT_SECONDS = 30.0
_LOAD_SLOT = BoundedSemaphore(1)
_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="journal-prices")


class _PriceDeadlineExceeded(TimeoutError):
    def __init__(self):
        super().__init__("Journal price loading deadline exceeded.")


def _load_prices(request, deadline):
    def check_deadline(*_status):
        # Ignore provider/status text. The sequential loader invokes this
        # callback before _load_ticker, inside its shared cache-lock context.
        # Raising unwinds that context instead of starting another symbol.
        if monotonic() >= deadline:
            raise _PriceDeadlineExceeded()

    check_deadline()
    # Keep the existing cache directory, failure cache, and bounded sequential
    # loading. The builder caps the request at 50 symbols, including SPY.
    result = load_market_data(
        request["tickers"], request["start_date"], request["end_date"],
        max_workers=1, status_callback=check_deadline,
    )
    check_deadline()
    return result


def _consume_exception(future):
    # A timed-out/disconnected caller will not retrieve a later worker error.
    # Retrieve it without logging provider text, credentials, or user data.
    if not future.cancelled():
        future.exception()


def _unavailable(result, status, warning):
    result["price_load_status"] = status
    result["warnings"].append(warning)
    return result


async def load_journal(history_rows) -> dict:
    """Load bounded cached prices and return the builder's journal plus warnings.

    All price-load failures degrade to the original no-price journal (null
    outcomes with the builder's reasons). Partial histories retain valid
    outcomes, with explicit warnings. Caller cancellation still propagates.
    """
    baseline = build_recommendation_journal(history_rows)
    baseline["warnings"] = []
    if baseline["deferred_symbols"]:
        baseline["warnings"].append(
            "The symbol limit deferred some price histories; their recommendations remain in the journal."
        )
    request = baseline["fetch_request"]
    if request is None:
        baseline["price_load_status"] = "not_needed"
        return baseline
    slot = _LOAD_SLOT
    if not slot.acquire(blocking=False):
        return _unavailable(baseline, "busy", "A journal price load is still running. Retry explicitly after it finishes.")
    try:
        # Include executor scheduling and cache-lock waiting in the budget;
        # do not reset it when the worker finally starts loading a ticker.
        deadline = monotonic() + PRICE_LOAD_TIMEOUT_SECONDS
        worker = _EXECUTOR.submit(_load_prices, request, deadline)
    except Exception:
        slot.release()
        return _unavailable(baseline, "unavailable", "Journal prices could not be loaded; outcomes remain unavailable.")
    # This callback belongs to the concurrent future, not an asyncio task.
    # It runs only when the real work finishes (or is cancelled before starting),
    # including after the requesting event loop has closed. Never release this
    # slot in an async finally block: timeout cannot stop a running price thread.
    worker.add_done_callback(lambda _finished: slot.release())
    pending = asyncio.wrap_future(worker)
    pending.add_done_callback(_consume_exception)
    try:
        prices, report = await asyncio.wait_for(
            asyncio.shield(pending), timeout=max(0, deadline - monotonic()),
        )
        if not isinstance(prices, dict) or not isinstance(report, dict):
            raise ValueError("Invalid market loader result")
        # Use the frozen input and cutoff from before the await, not potentially
        # mutated caller data or a newer market session after a slow load.
        result = build_recommendation_journal(
            baseline["source_history"], prices, asof=baseline["asof"],
        )
    except asyncio.TimeoutError:
        return _unavailable(baseline, "timed_out", "Journal price loading timed out. The worker retains its slot until finished; retry explicitly later.")
    except Exception:
        return _unavailable(baseline, "unavailable", "Journal prices could not be loaded; outcomes remain unavailable.")
    result["warnings"] = list(baseline["warnings"])
    partial = bool(report.get("Data Source Failures")) or any(
        outcome["status"] == "unavailable"
        for entry in result["entries"] for outcome in entry["outcomes"]
    ) or any(getattr(prices.get(symbol), "empty", True) for symbol in request["tickers"])
    if partial:
        result["warnings"].append(
            "Some price histories or outcomes are unavailable. Null outcomes retain their reasons; no dates or prices were substituted."
        )
    result["price_load_status"] = "partial" if partial else "loaded"
    return result
