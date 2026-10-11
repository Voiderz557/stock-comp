"""Read-only, deterministic TradingView-style long sizing from dated OHLCV.

This is an advisory calculation, not an order or a claim of optimal sizing.
The website passes only candidates from a current all-strategy consensus plan.
"""

from __future__ import annotations

import math
from datetime import date, timedelta

import pandas as pd


ATR_DAYS = 14
MIN_HISTORY_ROWS = 21
HISTORY_CALENDAR_DAYS = 120
MAX_QUOTE_AGE_DAYS = 5
ATR_STOP_MULTIPLIER = 2.0
MIN_STOP_FRACTION = 0.03
MAX_STOP_FRACTION = 0.12
RISK_BUDGET_FRACTION = 0.01
MAX_POSITION_EQUITY_FRACTION = 0.20
REWARD_TO_RISK = 2.0


class AutoPlanUnavailable(ValueError):
    """The requested plan cannot be calculated honestly from available data."""


def calculate_auto_long_plan(
    ticker: str,
    frame: pd.DataFrame,
    *,
    available_cash: float,
    account_equity: float,
    as_of: date,
) -> dict:
    """Use only rows dated through ``as_of``; never substitute cost basis.

    The stop is two ATR(14) below the dated close, clamped to 3–12%.
    Shares are the smallest whole-share limit from cash, 20%-of-equity
    concentration, and 1%-of-equity planned loss at that stop.
    """
    if not math.isfinite(available_cash) or available_cash < 0:
        raise AutoPlanUnavailable("Available paper cash must be a nonnegative finite amount.")
    if not math.isfinite(account_equity) or account_equity <= 0:
        raise AutoPlanUnavailable("Paper account equity must be a positive finite amount.")
    if frame is None or frame.empty or not {"High", "Low", "Close"}.issubset(frame.columns):
        raise AutoPlanUnavailable("Dated high, low, and close history is unavailable for this ticker.")

    try:
        dated_index = pd.to_datetime(frame.index)
    except (TypeError, ValueError) as exc:
        raise AutoPlanUnavailable("Price history has invalid trading dates.") from exc
    if dated_index.has_duplicates:
        raise AutoPlanUnavailable("Price history has duplicate trading dates.")
    history = frame.loc[dated_index.date <= as_of, ["High", "Low", "Close"]].copy()
    history = history.sort_index()
    if len(history) < MIN_HISTORY_ROWS:
        raise AutoPlanUnavailable("At least 21 dated trading rows are needed for automatic sizing.")
    history = history.tail(max(MIN_HISTORY_ROWS, ATR_DAYS + 1))
    if history.isna().any().any() or not history.apply(pd.to_numeric, errors="coerce").notna().all().all():
        raise AutoPlanUnavailable("Price history contains missing or nonnumeric values.")
    high, low, close = (history[column].astype(float) for column in ("High", "Low", "Close"))
    if (high <= 0).any() or (low <= 0).any() or (close <= 0).any() or (high < low).any() \
       or (high < close).any() or (low > close).any() \
       or not all(math.isfinite(float(value)) for value in (high.max(), low.min(), close.iloc[-1])):
        raise AutoPlanUnavailable("Price history contains invalid high, low, or close values.")
    quote_date = pd.Timestamp(history.index[-1]).date()
    if quote_date > as_of or (as_of - quote_date).days > MAX_QUOTE_AGE_DAYS:
        raise AutoPlanUnavailable("The latest daily close is stale; no position size was proposed.")

    previous_close = close.shift(1)
    true_range = pd.concat(
        [high - low, (high - previous_close).abs(), (low - previous_close).abs()], axis=1
    ).max(axis=1)
    atr = float(true_range.iloc[-ATR_DAYS:].mean())
    entry = float(close.iloc[-1])
    if not math.isfinite(atr) or atr <= 0:
        raise AutoPlanUnavailable("ATR is unavailable or zero; no stop or size was proposed.")
    stop_fraction = min(MAX_STOP_FRACTION, max(MIN_STOP_FRACTION, ATR_STOP_MULTIPLIER * atr / entry))
    risk_per_share = entry * stop_fraction
    # Use cent-valid, conservative US-equity reference levels for manual entry.
    # Rounding the stop UP cannot increase the modelled long-side loss.
    stop_price = math.ceil(entry * (1 - stop_fraction) * 100 - 1e-9) / 100
    target_price = math.floor(entry * (1 + stop_fraction * REWARD_TO_RISK) * 100 + 1e-9) / 100
    if stop_price >= entry or target_price <= entry:
        raise AutoPlanUnavailable("The price is too small for safe cent-denominated exit levels.")
    risk_budget = account_equity * RISK_BUDGET_FRACTION
    concentration_cap = account_equity * MAX_POSITION_EQUITY_FRACTION
    shares = min(
        math.floor(available_cash / entry),
        math.floor(concentration_cap / entry),
        math.floor(risk_budget / risk_per_share),
    )
    return {
        "ticker": ticker,
        "price_as_of": quote_date.isoformat(),
        "entry_price": round(entry, 4),
        "atr_14": round(atr, 4),
        "stop_distance_pct": round(stop_fraction * 100, 2),
        "stop_watch_price": round(stop_price, 2),
        "take_profit_watch_price": round(target_price, 2),
        "shares": shares,
        "position_value": round(shares * entry, 2),
        "cash_remaining": round(available_cash - shares * entry, 2),
        "risk_budget": round(risk_budget, 2),
        "planned_risk_at_stop": round(shares * (entry - stop_price), 2),
        "warning": (
            "Daily-close research estimate only. Stop and target are watch levels, not orders. "
            "A gap, slippage, or delayed execution can lose more than the planned risk. "
            "No TradingView account is connected or changed."
        ),
    }


def load_auto_long_plan(
    ticker: str,
    *,
    available_cash: float,
    account_equity: float,
    as_of: date | None = None,
    load_market_data_fn=None,
) -> dict:
    """Load one ticker's historical daily rows through the existing data layer."""
    if load_market_data_fn is None:
        from data.market_data import load_market_data as load_market_data_fn

    evaluation_date = as_of or date.today()
    frames, _ = load_market_data_fn(
        [ticker], evaluation_date - timedelta(days=HISTORY_CALENDAR_DAYS), evaluation_date
    )
    return calculate_auto_long_plan(
        ticker, frames.get(ticker), available_cash=available_cash,
        account_equity=account_equity, as_of=evaluation_date,
    )
