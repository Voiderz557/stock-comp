"""Recommendation-only daily trading plan for the competition dashboard.

This module never opens or closes paper positions. It ranks one registered
rule-based strategy (or the regime selector's first available preference),
reviews existing holdings, and proposes LONG allocations that fit the same
cash, shared gross-capacity, $5 minimum price, and $20k long-cap checks
used at storage time. Proposed exits are never treated as completed.
"""

from __future__ import annotations

import csv
import io

import pandas as pd

from config import MAX_POSITION_VALUE, MIN_STOCK_PRICE, STOCK_UNIVERSE
from market.regime import REQUIRED_HISTORY_DAYS as REGIME_REQUIRED_HISTORY_DAYS
from market.regime import classify_market_regime, compute_market_features
from market.strategy_selector import describe_availability, recommend_strategy
from paper_trading.portfolio import (
    compute_exposure_summary,
    is_valid_market_price,
    same_ticker_market_value,
)
from paper_trading.prices import SOURCE_LABEL, latest_daily_close_quote
from strategies.registry import (
    available_strategy_names,
    extra_strategy_benchmark_tickers,
    get_strategy,
    invoke_analyze,
)

AUTOMATIC_MODE = "Automatic — market regime"
KEEP = "KEEP"
REVIEW_EXIT = "REVIEW EXIT"
UNAVAILABLE = "UNAVAILABLE"

TRADING_TO_CALENDAR_DAY_MULTIPLIER = 1.6
SCAN_HISTORY_BUFFER_CALENDAR_DAYS = 30
ALLOCATION_EPSILON = 1e-6


def calendar_days_for_trading_days(trading_days):
    return int(trading_days * TRADING_TO_CALENDAR_DAY_MULTIPLIER) + SCAN_HISTORY_BUFFER_CALENDAR_DAYS


def plan_mode_options():
    return [AUTOMATIC_MODE, *available_strategy_names()]


def resolve_plan_strategy(mode, regime_result=None, get_strategy_fn=get_strategy):
    """Return the strategy used for ranking and holdings review.

    Automatic mode uses `recommend_strategy` and the first *available*
    preferred name. It does not invent a fallback ranking formula.
    """
    if mode != AUTOMATIC_MODE:
        definition = get_strategy_fn(mode)
        return {
            "mode": mode,
            "strategy_name": mode,
            "definition": definition,
            "strategy_reason": "User-selected registered rule-based strategy.",
            "recommendation": None,
            "regime_name": None,
        }

    if regime_result is None:
        raise ValueError(
            "Automatic mode needs a market-regime classification before a "
            "strategy can be selected."
        )
    recommendation = recommend_strategy(regime_result)
    availability = describe_availability(recommendation.preferred_strategies)
    chosen = next((name for name, is_available in availability if is_available), None)
    definition = get_strategy_fn(chosen) if chosen is not None else None
    return {
        "mode": AUTOMATIC_MODE,
        "strategy_name": chosen,
        "definition": definition,
        "strategy_reason": recommendation.reason,
        "recommendation": recommendation,
        "regime_name": recommendation.regime,
    }


def analyze_ticker(definition, ticker, price_data):
    """Call one strategy's public analyze() with optional benchmark history."""
    if definition is None:
        return None
    frame = (price_data or {}).get(ticker)
    if frame is None or getattr(frame, "empty", True):
        return None
    preferred = getattr(definition, "benchmark_ticker", None)
    relative = (price_data or {}).get(preferred) if preferred else None
    try:
        return invoke_analyze(
            definition.analyze, ticker, frame, benchmark_data=relative
        )
    except Exception:
        return None


def _quote_for(ticker, price_data):
    return latest_daily_close_quote((price_data or {}).get(ticker))


def _history_requirement(definition, include_regime):
    days = REGIME_REQUIRED_HISTORY_DAYS if include_regime else 0
    if definition is not None:
        days = max(days, int(definition.required_history_days))
    return days


def scan_tickers_for_plan(universe, open_positions, definition, include_regime=False):
    tickers = list(dict.fromkeys(list(universe or ())))
    for position in open_positions or []:
        ticker = position.get("ticker")
        if ticker and ticker not in tickers:
            tickers.append(ticker)
    tickers.extend(extra_strategy_benchmark_tickers())
    if include_regime or getattr(definition, "benchmark_ticker", None):
        for extra in ("SPY", "QQQ"):
            if extra not in tickers:
                tickers.append(extra)
    return tickers


def _room_under_long_cap(open_positions, ticker, current_prices, max_position_value):
    existing = same_ticker_market_value(
        open_positions, ticker, "LONG", current_prices
    )
    return max(0.0, float(max_position_value) - existing), existing


def allocate_buy_candidates(
    ranked_candidates,
    account_state,
    open_positions,
    quotes,
    min_stock_price=MIN_STOCK_PRICE,
    max_position_value=MAX_POSITION_VALUE,
):
    """Walk BUY candidates in rank order and spend one shared remaining budget."""
    cash = float(account_state["Cash"])
    prices = {ticker: quote["price"] for ticker, quote in (quotes or {}).items()}
    skipped = []
    buys = []

    try:
        exposure = compute_exposure_summary(
            open_positions, prices, account_state["Starting Capital"]
        )
        remaining_capacity = float(exposure["Remaining Capacity"])
        capacity_error = None
    except ValueError as error:
        return {
            "buys": [],
            "skipped": [
                {
                    "Ticker": None,
                    "Reason": (
                        "Cannot allocate purchases because shared capacity "
                        f"could not be marked to market: {error}"
                    ),
                }
            ],
            "cash_before": cash,
            "cash_after": cash,
            "remaining_capacity_before": None,
            "remaining_capacity_after": None,
            "capacity_error": str(error),
        }

    remaining_cash = cash
    remaining_capacity_left = remaining_capacity

    for candidate in ranked_candidates:
        ticker = candidate["Ticker"]
        quote = (quotes or {}).get(ticker)
        if quote is None or not is_valid_market_price(quote.get("price")):
            skipped.append(
                {
                    "Ticker": ticker,
                    "Reason": (
                        f"Latest daily close is unavailable for {ticker}. "
                        "No purchase is proposed."
                    ),
                }
            )
            continue
        price = float(quote["price"])
        if price < float(min_stock_price) - 1e-9:
            skipped.append(
                {
                    "Ticker": ticker,
                    "Reason": (
                        f"Price ${price:,.2f} is below the ${float(min_stock_price):,.2f} "
                        "minimum stock price."
                    ),
                }
            )
            continue
        try:
            room_under_cap, existing_value = _room_under_long_cap(
                open_positions, ticker, prices, max_position_value
            )
        except ValueError as error:
            skipped.append({"Ticker": ticker, "Reason": str(error)})
            continue

        budget = min(remaining_cash, remaining_capacity_left, room_under_cap)
        if budget <= ALLOCATION_EPSILON:
            if room_under_cap <= ALLOCATION_EPSILON:
                skipped.append(
                    {
                        "Ticker": ticker,
                        "Reason": (
                            f"Per-stock long cap ${float(max_position_value):,.2f} is "
                            f"already filled by existing {ticker} lots "
                            f"(${existing_value:,.2f}). Appreciation is left open; "
                            "no additional purchase is proposed."
                        ),
                    }
                )
            elif remaining_capacity_left <= ALLOCATION_EPSILON:
                skipped.append(
                    {
                        "Ticker": ticker,
                        "Reason": "Shared remaining capacity is exhausted.",
                    }
                )
            else:
                skipped.append(
                    {
                        "Ticker": ticker,
                        "Reason": "Available cash is exhausted.",
                    }
                )
            continue

        quantity = budget / price
        allocation = quantity * price
        buys.append(
            {
                "Ticker": ticker,
                "Signal": "BUY",
                "Score": candidate.get("Score"),
                "Reason": candidate.get("Reason", ""),
                "Price": price,
                "Price As Of": quote.get("as_of"),
                "Price Source": quote.get("source_label", SOURCE_LABEL),
                "Quantity": quantity,
                "Allocation": allocation,
            }
        )
        remaining_cash -= allocation
        remaining_capacity_left -= allocation

    return {
        "buys": buys,
        "skipped": skipped,
        "cash_before": cash,
        "cash_after": remaining_cash,
        "remaining_capacity_before": remaining_capacity,
        "remaining_capacity_after": remaining_capacity_left,
        "capacity_error": capacity_error,
    }


def review_holdings(open_positions, definition, price_data):
    """Label each open lot KEEP / REVIEW EXIT / UNAVAILABLE. Never infers an exit."""
    reviews = []
    for position in open_positions or []:
        ticker = position["ticker"]
        result = analyze_ticker(definition, ticker, price_data)
        quote = _quote_for(ticker, price_data)
        if definition is None:
            reviews.append(
                {
                    **position,
                    "Signal": None,
                    "Action": UNAVAILABLE,
                    "Reason": (
                        "No available preferred strategy is selected, so this "
                        "holding cannot be reviewed. An exit is not inferred."
                    ),
                    "Score": None,
                    "Price": None if quote is None else quote["price"],
                    "Price As Of": None if quote is None else quote["as_of"],
                }
            )
            continue
        if result is None:
            reviews.append(
                {
                    **position,
                    "Signal": None,
                    "Action": UNAVAILABLE,
                    "Reason": (
                        f"Strategy output is unavailable for {ticker} "
                        "(missing history, benchmark context, or analyze() "
                        "returned no result). An exit is not inferred."
                    ),
                    "Score": None,
                    "Price": None if quote is None else quote["price"],
                    "Price As Of": None if quote is None else quote["as_of"],
                }
            )
            continue
        signal = result.get("Signal")
        reason = result.get("Reason", "")
        if signal == "AVOID":
            action = REVIEW_EXIT
        elif signal in ("BUY", "WAIT"):
            action = KEEP
        else:
            action = UNAVAILABLE
            reason = (
                f"Unrecognized signal {signal!r} for {ticker}. "
                "An exit is not inferred."
            )
        reviews.append(
            {
                **position,
                "Signal": signal,
                "Action": action,
                "Reason": reason,
                "Score": result.get("Score"),
                "Price": None if quote is None else quote["price"],
                "Price As Of": None if quote is None else quote["as_of"],
            }
        )
    return reviews


def _no_purchase_explanation(resolved, allocation, ranked_candidates):
    if resolved["strategy_name"] is None:
        recommendation = resolved.get("recommendation")
        extra = ""
        if recommendation is not None:
            extra = f" {recommendation.reason}"
            if recommendation.notes:
                extra += " " + " ".join(recommendation.notes)
        return (
            "No purchases qualify: Automatic mode has no available preferred "
            "long strategy for this regime." + extra
        )
    if allocation.get("capacity_error"):
        return (
            "No purchases qualify because shared capacity could not be computed. "
            + allocation["capacity_error"]
        )
    if not ranked_candidates:
        return (
            f"No purchases qualify: {resolved['strategy_name']} produced no "
            f"BUY signals at or above ${MIN_STOCK_PRICE:,.2f}."
        )
    if not allocation["buys"]:
        reasons = [item["Reason"] for item in allocation["skipped"] if item.get("Reason")]
        detail = reasons[0] if reasons else "cash, capacity, or the $20k long-position cap blocked every candidate."
        return "No purchases qualify. " + detail
    return None


def build_trading_plan(
    mode,
    account_state,
    open_positions,
    price_data,
    universe,
    regime_result=None,
    get_strategy_fn=get_strategy,
    min_stock_price=MIN_STOCK_PRICE,
    max_position_value=MAX_POSITION_VALUE,
):
    """Pure plan builder: no I/O and no ledger mutations."""
    resolved = resolve_plan_strategy(mode, regime_result, get_strategy_fn=get_strategy_fn)
    definition = resolved["definition"]
    quotes = {}
    ranked = []
    unavailable_universe = []

    if definition is not None:
        scored = []
        for ticker in universe:
            result = analyze_ticker(definition, ticker, price_data)
            quote = _quote_for(ticker, price_data)
            if quote is not None:
                quotes[ticker] = quote
            if result is None:
                if ticker not in extra_strategy_benchmark_tickers() and ticker not in ("SPY", "QQQ"):
                    unavailable_universe.append(ticker)
                continue
            if result.get("Signal") != "BUY":
                continue
            scored.append(result)
        scored.sort(key=definition.rank_key, reverse=True)
        ranked = scored

    for position in open_positions or []:
        ticker = position.get("ticker")
        if ticker and ticker not in quotes:
            quote = _quote_for(ticker, price_data)
            if quote is not None:
                quotes[ticker] = quote

    allocation = allocate_buy_candidates(
        ranked,
        account_state,
        open_positions,
        quotes,
        min_stock_price=min_stock_price,
        max_position_value=max_position_value,
    )
    holdings = review_holdings(open_positions, definition, price_data)
    return {
        "mode": resolved["mode"],
        "strategy_name": resolved["strategy_name"],
        "strategy_reason": resolved["strategy_reason"],
        "regime_name": resolved["regime_name"],
        "recommendation": resolved["recommendation"],
        "price_source": SOURCE_LABEL,
        "buys": allocation["buys"],
        "skipped_buys": allocation["skipped"],
        "holdings": holdings,
        "cash_before": allocation["cash_before"],
        "cash_after": allocation["cash_after"],
        "remaining_capacity_before": allocation["remaining_capacity_before"],
        "remaining_capacity_after": allocation["remaining_capacity_after"],
        "no_purchase_explanation": _no_purchase_explanation(resolved, allocation, ranked),
        "unavailable_universe": unavailable_universe,
        "ranked_buy_count": len(ranked),
    }


def plan_to_csv(plan):
    """Serialize the displayed plan to CSV text."""
    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=[
            "Section",
            "Ticker",
            "Action",
            "Signal",
            "Score",
            "Price",
            "Price As Of",
            "Quantity",
            "Allocation",
            "Reason",
        ],
    )
    writer.writeheader()
    for row in plan.get("buys") or []:
        writer.writerow(
            {
                "Section": "Proposed BUY",
                "Ticker": row["Ticker"],
                "Action": "BUY",
                "Signal": row.get("Signal"),
                "Score": row.get("Score"),
                "Price": row.get("Price"),
                "Price As Of": row.get("Price As Of"),
                "Quantity": row.get("Quantity"),
                "Allocation": row.get("Allocation"),
                "Reason": row.get("Reason"),
            }
        )
    for row in plan.get("holdings") or []:
        writer.writerow(
            {
                "Section": "Holding review",
                "Ticker": row.get("ticker"),
                "Action": row.get("Action"),
                "Signal": row.get("Signal"),
                "Score": row.get("Score"),
                "Price": row.get("Price"),
                "Price As Of": row.get("Price As Of"),
                "Quantity": row.get("quantity"),
                "Allocation": row.get("allocated_capital"),
                "Reason": row.get("Reason"),
            }
        )
    writer.writerow(
        {
            "Section": "Cash after proposed buys",
            "Ticker": "",
            "Action": "",
            "Signal": "",
            "Score": "",
            "Price": "",
            "Price As Of": "",
            "Quantity": "",
            "Allocation": plan.get("cash_after"),
            "Reason": plan.get("no_purchase_explanation") or "",
        }
    )
    return output.getvalue()


def load_and_build_trading_plan(
    mode,
    account_state,
    open_positions,
    universe=None,
    load_market_data_fn=None,
    as_of_date=None,
    get_strategy_fn=get_strategy,
    min_stock_price=MIN_STOCK_PRICE,
    max_position_value=MAX_POSITION_VALUE,
    status_callback=None,
):
    """Load required history, optionally classify the regime, then build a plan."""
    if load_market_data_fn is None:
        from data.market_data import load_market_data as load_market_data_fn

    universe = list(universe if universe is not None else STOCK_UNIVERSE)
    as_of = (
        pd.Timestamp(as_of_date).normalize()
        if as_of_date is not None
        else pd.Timestamp.today().normalize()
    )
    regime_result = None
    include_regime = mode == AUTOMATIC_MODE
    definition = None
    if mode != AUTOMATIC_MODE:
        definition = get_strategy_fn(mode)

    if include_regime:
        if status_callback:
            status_callback("Detecting market regime", "SPY/QQQ")
        from market.regime import LIVE_LOOKBACK_CALENDAR_DAYS

        start = as_of - pd.Timedelta(days=LIVE_LOOKBACK_CALENDAR_DAYS)
        regime_data, _info = load_market_data_fn(
            ["SPY", "QQQ"], start, as_of, status_callback=status_callback
        )
        spy = regime_data.get("SPY")
        qqq = regime_data.get("QQQ")
        if spy is None or spy.empty or qqq is None or qqq.empty:
            raise ValueError(
                "Could not load sufficient SPY/QQQ data to classify the market regime."
            )
        regime_result = classify_market_regime(compute_market_features(spy, qqq))
        resolved = resolve_plan_strategy(
            AUTOMATIC_MODE, regime_result, get_strategy_fn=get_strategy_fn
        )
        definition = resolved["definition"]

    history_days = _history_requirement(definition, include_regime)
    start_date = as_of - pd.Timedelta(days=calendar_days_for_trading_days(history_days))
    tickers = scan_tickers_for_plan(
        universe, open_positions, definition, include_regime=include_regime
    )
    if status_callback:
        status_callback("Loading price history", f"{len(tickers)} tickers")
    price_data, cache_info = load_market_data_fn(
        tickers, start_date, as_of, status_callback=status_callback
    )
    if include_regime and regime_result is None:
        raise ValueError("Automatic mode did not produce a regime classification.")

    if status_callback:
        status_callback("Building trading plan", definition.name if definition else "no strategy")
    plan = build_trading_plan(
        mode,
        account_state,
        open_positions,
        price_data,
        universe,
        regime_result=regime_result,
        get_strategy_fn=get_strategy_fn,
        min_stock_price=min_stock_price,
        max_position_value=max_position_value,
    )
    plan["cache_info"] = cache_info
    plan["requested_as_of"] = str(as_of.date())
    plan["history_start"] = str(start_date.date())
    session_start, session_end = _price_session_bounds(plan)
    plan["price_session_start"] = session_start
    plan["price_session_end"] = session_end
    plan["data_warnings"] = _plan_data_warnings(plan)
    return plan


def _price_session_bounds(plan):
    stamps = []
    for row in list(plan.get("buys") or []) + list(plan.get("holdings") or []):
        raw = row.get("Price As Of") or row.get("price_as_of")
        if not raw:
            continue
        stamps.append(pd.Timestamp(raw))
    if not stamps:
        requested = plan.get("requested_as_of")
        return requested, requested
    return str(min(stamps).date()), str(max(stamps).date())


def _plan_data_warnings(plan):
    warnings = []
    for ticker in plan.get("unavailable_universe") or []:
        warnings.append(f"{ticker}: price history unavailable; not treated as a sell.")
    for item in plan.get("skipped_buys") or []:
        warnings.append(f"{item.get('Ticker')}: {item.get('Reason')}")
    cache = plan.get("cache_info") or {}
    for failure in cache.get("Data Source Failures") or []:
        warnings.append(
            f"{failure.get('Ticker')}: {failure.get('Reason')} "
            f"({failure.get('Requested Start')} to {failure.get('Requested End')})"
        )
    return warnings
