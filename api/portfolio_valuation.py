"""Read-only daily-close marks; never write holdings, basis, cash or history.

Short equity uses the existing paper engine's collateral convention, not a
broker margin-account convention. Missing marks/basis remain unknown.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from math import isfinite
from threading import BoundedSemaphore
from time import monotonic

import pandas as pd

from data.market_data import load_market_data
from paper_trading.portfolio import compute_position_metrics
from paper_trading.prices import latest_daily_close_quote, latest_completed_close_date

TIMEOUT_SECONDS = 30
MAX_SYMBOLS = 100
MAX_PRICE_AGE_DAYS = 4
_SLOT = BoundedSemaphore(1)
_POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="portfolio-marks")


def _number(value, *, positive=False):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and isfinite(value) and (value > 0 if positive else value >= 0))


def value_portfolio(snapshot, frames, *, now=None):
    """Pure, request-local valuation of an immutable account observation."""
    now = now or datetime.now(timezone.utc)
    positions = deepcopy(snapshot.get("positions") or [])
    warnings = []
    values, pnls, longs, shorts = [], [], [], []
    for row in positions:
        ticker, side, quantity = row["ticker"], row["side"], row["quantity"]
        row.update(price=None, price_as_of=None, value=None, pnl=None, weight=None)
        quote = latest_daily_close_quote(frames.get(ticker), now=now)
        if quote:
            row["price_as_of"] = quote["as_of"]
            age = (pd.Timestamp(now).date() - pd.Timestamp(quote["as_of"]).date()).days
            if age > MAX_PRICE_AGE_DAYS:
                warnings.append(f"{ticker}: last available close is stale ({quote['as_of'][:10]}); valuation unavailable.")
                quote = None
        if not quote:
            warnings.append(f"{ticker}: no usable recent completed daily close; no entry-price fallback.")
        elif not _number(quantity, positive=True) or side not in {"LONG", "SHORT"}:
            warnings.append(f"{ticker}: quantity or side is invalid; valuation unavailable.")
        else:
            price = quote["price"]
            row.update(price=price, price_as_of=quote["as_of"])
            basis = row.get("exact_cost_basis")
            if basis is None and _number(row.get("average_price")):
                basis = row["average_price"] * quantity
            notional = quantity * price
            if _number(basis):
                metrics = compute_position_metrics({"ticker": ticker, "direction": side,
                    "quantity": quantity, "entry_price": basis / quantity,
                    "allocated_capital": basis}, price)
                row.update(value=metrics["Market Value"], pnl=metrics["Unrealized P&L"])
            else:
                row["value"] = notional if side == "LONG" else None
                warnings.append(f"{ticker}: cost basis unavailable; P&L is unknown.")
        values.append(row["value"])
        pnls.append(row["pnl"])
        exposure = quantity * row["price"] if row["price"] is not None and _number(quantity, positive=True) else None
        (longs if side == "LONG" else shorts).append(exposure)
    def complete_sum(items):
        return sum(items) if all(v is not None and isfinite(v) for v in items) else None
    cash = snapshot["portfolio"]["cash"]
    held_value = complete_sum(values)
    equity = cash + held_value if _number(cash) and held_value is not None else None
    for row in positions:
        if equity is not None and equity > 0 and row["value"] is not None:
            row["weight"] = 100 * row["value"] / equity
    return {"portfolio_version": snapshot.get("account", {}).get("portfolio_version"),
            "as_of": pd.Timestamp(now).isoformat(),
            "source": "Latest available completed daily close (not live intraday)",
            "warnings": warnings, "positions": positions,
            "portfolio": {"cash": cash, "value": equity, "long_exposure": complete_sum(longs),
                          "short_exposure": complete_sum(shorts), "unrealized_pnl": complete_sum(pnls)}}


async def load_portfolio_valuation(snapshot):
    frozen = deepcopy(snapshot)
    now = datetime.now(timezone.utc)
    baseline = value_portfolio(frozen, {}, now=now)
    symbols = sorted({row["ticker"] for row in frozen.get("positions", [])})
    if not symbols:
        return baseline
    if len(symbols) > MAX_SYMBOLS or not _SLOT.acquire(blocking=False):
        baseline["warnings"].append("Price refresh is busy or exceeds the symbol limit. Try again later.")
        return baseline
    deadline = monotonic() + TIMEOUT_SECONDS
    def check(*_):
        if monotonic() >= deadline:
            raise TimeoutError("Price refresh deadline exceeded")
    def work():
        check()
        end = latest_completed_close_date(now)
        return load_market_data(symbols, end - timedelta(days=14), end,
                                status_callback=check, max_workers=1)
    try:
        worker = _POOL.submit(work)
    except Exception:
        _SLOT.release()
        baseline["warnings"].append("Price refresh unavailable. No account data changed.")
        return baseline
    worker.add_done_callback(lambda _: _SLOT.release())
    pending = asyncio.wrap_future(worker)
    pending.add_done_callback(lambda future: None if future.cancelled() else future.exception())
    try:
        frames, _report = await asyncio.wait_for(asyncio.shield(pending), TIMEOUT_SECONDS)
        return value_portfolio(frozen, frames, now=now)
    except Exception:
        baseline["warnings"].append("Price refresh failed or timed out. No account data changed.")
        return baseline
