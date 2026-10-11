"""Recommendation-only daily trading plan for the competition dashboard.

This module never opens or closes paper positions. It ranks one registered
rule-based strategy, the regime selector's first available preference, or a
rank-based consensus of all live-enabled strategies. It reviews existing
holdings and proposes LONG allocations that fit the same cash, shared
gross-capacity, $5 minimum price, and $20k long-cap checks used at storage
time. Proposed exits are never treated as completed.
"""

from __future__ import annotations

import csv
import io
import math
from datetime import datetime, timezone
from functools import partial
from time import perf_counter

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
from paper_trading.prices import SOURCE_LABEL, completed_daily_close_frame, latest_daily_close_quote
from paper_trading.scan_coverage import summarize_scan_coverage
from strategies.registry import (
    extra_strategy_benchmark_tickers,
    get_strategy,
    invoke_analyze,
    live_strategy_names,
)

AUTOMATIC_MODE = "Automatic — market regime"
CONSENSUS_MODE = "All strategies — consensus"
DEFAULT_TARGET_POSITION_VALUE = 10_000.0
MIN_CONSENSUS_BUYS = 2
MAX_CONSENSUS_AVOIDS = 2
HIGH_VOLATILITY_MIN_BUYS = 3
KEEP = "KEEP"
REVIEW_EXIT = "REVIEW EXIT"
UNAVAILABLE = "UNAVAILABLE"

TRADING_TO_CALENDAR_DAY_MULTIPLIER = 1.6
SCAN_HISTORY_BUFFER_CALENDAR_DAYS = 30
ALLOCATION_EPSILON = 1e-6
LIVE_SCANNER_PRICE_WORKERS = 4


def calendar_days_for_trading_days(trading_days):
    return int(trading_days * TRADING_TO_CALENDAR_DAY_MULTIPLIER) + SCAN_HISTORY_BUFFER_CALENDAR_DAYS


def plan_mode_options():
    return [CONSENSUS_MODE, AUTOMATIC_MODE, *live_strategy_names()]


def resolve_plan_strategy(mode, regime_result=None, get_strategy_fn=get_strategy):
    """Return the strategy used for ranking and holdings review.

    Automatic mode uses `recommend_strategy` and the first *available*
    preferred name. It does not invent a fallback ranking formula.
    """
    if mode == CONSENSUS_MODE:
        if regime_result is None:
            raise ValueError("All-strategy consensus needs a market-regime classification.")
        recommendation = recommend_strategy(regime_result)
        return {
            "mode": mode,
            "strategy_name": mode,
            "definition": None,
            "strategy_reason": recommendation.reason,
            "recommendation": recommendation,
            "regime_name": recommendation.regime,
        }
    if mode != AUTOMATIC_MODE:
        definition = get_strategy_fn(mode)
        if not getattr(definition, "live_enabled", True):
            raise ValueError(f"Strategy {mode!r} is not enabled for live paper planning.")
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
        result = invoke_analyze(
            definition.analyze, ticker, frame, benchmark_data=relative
        )
        # Some strategies discard a row with a missing factor (for example
        # Volume). Never attach today's quote to a result actually calculated
        # from an older close. The live registry strategies expose the price
        # used for scoring in Factor Details; synthetic legacy adapters that
        # omit it retain their existing contract.
        if isinstance(result, dict):
            details = result.get("Factor Details") or {}
            if isinstance(details, dict) and "Price" in details:
                quote = _quote_for(ticker, price_data)
                factor_price = float(details["Price"])
                if quote is None or not math.isfinite(factor_price) or not math.isclose(
                    factor_price, quote["price"], rel_tol=1e-8, abs_tol=1e-8
                ):
                    return None
        return result
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


def _quote_session(quote):
    if not quote or quote.get("as_of") is None:
        return None
    try:
        parsed = pd.Timestamp(quote["as_of"])
        return None if pd.isna(parsed) else parsed.date()
    except (TypeError, ValueError):
        return None


def allocate_buy_candidates(
    ranked_candidates,
    account_state,
    open_positions,
    quotes,
    min_stock_price=MIN_STOCK_PRICE,
    max_position_value=MAX_POSITION_VALUE,
    target_position_value=None,
    whole_shares=False,
    reference_quote_as_of=None,
):
    """Walk BUY candidates in rank order and spend one shared remaining budget."""
    if target_position_value is not None and (
        not math.isfinite(float(target_position_value))
        or float(target_position_value) <= 0
        or float(target_position_value) > float(max_position_value)
    ):
        raise ValueError("Per-stock target must be above $0 and at most the $20,000 cap.")
    cash = float(account_state["Cash"])
    prices = {ticker: quote["price"] for ticker, quote in (quotes or {}).items()}
    skipped = []
    buys = []
    reference_session = pd.Timestamp(reference_quote_as_of).date() if reference_quote_as_of else None

    if reference_session is not None:
        stale_holdings = sorted({
            position["ticker"] for position in open_positions or []
            if _quote_session((quotes or {}).get(position["ticker"])) is None
            or _quote_session((quotes or {}).get(position["ticker"])) < reference_session
        })
        if stale_holdings:
            reason = (
                "Cannot allocate purchases because existing holdings lack a quote for "
                f"market reference session {reference_session}: {', '.join(stale_holdings)}."
            )
            return {
                "buys": [], "skipped": [{"Ticker": None, "Reason": reason}],
                "cash_before": cash, "cash_after": cash,
                "remaining_capacity_before": None, "remaining_capacity_after": None,
                "capacity_error": reason,
            }

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
        quote_session = _quote_session(quote)
        if reference_session is not None and (quote_session is None or quote_session < reference_session):
            skipped.append({
                "Ticker": ticker,
                "Reason": (
                    f"Latest daily close is dated {quote_session or 'unknown'}, before the "
                    f"market reference session {reference_session}. No purchase is proposed."
                ),
            })
            continue
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

        budget = min(
            remaining_cash,
            remaining_capacity_left,
            room_under_cap,
            float(target_position_value) if target_position_value is not None else float(max_position_value),
        )
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

        quantity = math.floor(budget / price) if whole_shares else budget / price
        if quantity <= 0:
            skipped.append({"Ticker": ticker, "Reason": "Available per-stock budget is less than one whole share."})
            continue
        allocation = quantity * price
        buys.append(
            {
                "Ticker": ticker,
                "Signal": "BUY",
                "Score": candidate.get("Score"),
                "Reason": candidate.get("Reason", ""),
                "Factor Details": candidate.get("Factor Details", {}),
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
                "Factor Details": result.get("Factor Details", {}),
                "Price": None if quote is None else quote["price"],
                "Price As Of": None if quote is None else quote["as_of"],
            }
        )
    return reviews


def _consensus_scan(universe, open_positions, price_data, recommendation, get_strategy_fn):
    """Compare BUY ranks, not incompatible raw score scales, across all live strategies.

    Missing strategy output is not a WAIT vote: that ticker is excluded until
    every strategy has enough dated history (including benchmark context).
    """
    names = live_strategy_names()
    tickers = list(dict.fromkeys([*universe, *(p["ticker"] for p in open_positions or [])]))
    outputs = {ticker: {} for ticker in tickers}
    buy_rank_points = {ticker: [] for ticker in tickers}
    for name in names:
        definition = get_strategy_fn(name)
        buys = []
        for ticker in tickers:
            result = analyze_ticker(definition, ticker, price_data)
            outputs[ticker][name] = result
            if result is not None and result.get("Signal") == "BUY":
                buys.append(result)
        buys.sort(key=definition.rank_key, reverse=True)
        for index, result in enumerate(buys):
            buy_rank_points[result["Ticker"]].append(1.0 - index / len(buys))

    preferred = set(recommendation.preferred_strategies) & set(names)
    held_tickers = {position["ticker"] for position in open_positions or []}
    ranked = []
    unavailable = []
    for ticker in universe:
        results = outputs[ticker]
        if any(results[name] is None or results[name].get("Signal") not in {"BUY", "WAIT", "AVOID"} for name in names):
            unavailable.append(ticker)
            continue
        buys = [name for name in names if results[name]["Signal"] == "BUY"]
        avoids = [name for name in names if results[name]["Signal"] == "AVOID"]
        # Holding review recommends REVIEW EXIT at two AVOID votes. A plan
        # must never simultaneously propose increasing the same holding.
        if ticker in held_tickers and len(avoids) >= 2:
            continue
        minimum = HIGH_VOLATILITY_MIN_BUYS if recommendation.regime == "HIGH_VOLATILITY" else MIN_CONSENSUS_BUYS
        if not preferred or len(buys) < minimum or len(avoids) > MAX_CONSENSUS_AVOIDS:
            continue
        preferred_buys = len(set(buys) & preferred)
        if not preferred_buys:
            continue
        rank_strength = sum(buy_rank_points[ticker]) / len(buy_rank_points[ticker])
        score = 100.0 * (
            0.7 * len(buys) / len(names)
            + 0.2 * rank_strength
            + 0.1 * preferred_buys / len(preferred)
        )
        signals = {name: results[name]["Signal"] for name in names}
        ranked.append({
            "Ticker": ticker,
            "Signal": "BUY",
            "Score": round(score, 4),
            "Reason": (
                f"{len(buys)}/{len(names)} live strategies signal BUY; "
                f"{preferred_buys} preferred for {recommendation.regime}; "
                f"{len(avoids)} signal AVOID. BUY: {', '.join(buys)}. "
                "This is a cross-strategy ranking, not a trade order."
            ),
            "Factor Details": {
                "Strategy Signals": signals,
                "Strategy Scores": {name: results[name].get("Score") for name in names},
                "BUY Votes": len(buys),
                "AVOID Votes": len(avoids),
                "Preferred BUY Votes": preferred_buys,
                "Mean BUY Rank Strength": round(rank_strength, 4),
                "Consensus Score": round(score, 4),
            },
        })
    ranked.sort(key=lambda result: (-result["Score"], result["Ticker"]))
    return ranked, unavailable, outputs


def _review_consensus_holdings(open_positions, outputs, price_data):
    reviews = []
    for position in open_positions or []:
        ticker = position["ticker"]
        results = outputs.get(ticker, {})
        quote = _quote_for(ticker, price_data)
        if not results or any(
            result is None or result.get("Signal") not in {"BUY", "WAIT", "AVOID"}
            for result in results.values()
        ):
            action, reason, signal = UNAVAILABLE, "Not all live strategies could evaluate this holding; no exit is inferred.", None
            score = None
        else:
            avoids = [name for name, result in results.items() if result.get("Signal") == "AVOID"]
            action = REVIEW_EXIT if len(avoids) >= 2 else KEEP
            signal = "AVOID" if action == REVIEW_EXIT else "WAIT"
            reason = f"{len(avoids)}/{len(results)} strategies signal AVOID: {', '.join(avoids) or 'none'}. Review manually; no exit executes."
            score = 100.0 * sum(
                {"BUY": 1.0, "WAIT": 0.5, "AVOID": 0.0}[result["Signal"]]
                for result in results.values()
            ) / len(results)
        reviews.append({
            **position, "Action": action, "Signal": signal, "Reason": reason,
            "Score": score,
            "Factor Details": {
                "Strategy Signals": {
                    name: None if result is None else result.get("Signal")
                    for name, result in results.items()
                },
                "Holding Agreement Score": score,
            },
            "Price": None if quote is None else quote["price"],
            "Price As Of": None if quote is None else quote["as_of"],
        })
    return reviews


def _no_purchase_explanation(resolved, allocation, ranked_candidates, min_stock_price=MIN_STOCK_PRICE):
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
        if resolved["mode"] == CONSENSUS_MODE:
            return (
                "No purchases qualify: not enough live strategies agree on BUY "
                "with complete dated data in this market regime. Cash stays unallocated."
            )
        return (
            f"No purchases qualify: {resolved['strategy_name']} produced no "
            f"BUY signals at or above ${min_stock_price:,.2f}."
        )
    if not allocation["buys"]:
        reasons = [item["Reason"] for item in allocation["skipped"] if item.get("Reason")]
        detail = reasons[0] if reasons else "cash, capacity, or the $20k long-position cap blocked every candidate."
        return "No purchases qualify. " + detail
    return None


def _consensus_candidate_diagnostics(universe, outputs, ranked, recommendation, open_positions):
    """Explain selection outcomes without altering consensus votes or ranking."""
    qualified = {row["Ticker"] for row in ranked}
    preferred = set(recommendation.preferred_strategies) & set(live_strategy_names())
    minimum = HIGH_VOLATILITY_MIN_BUYS if recommendation.regime == "HIGH_VOLATILITY" else MIN_CONSENSUS_BUYS
    held_tickers = {position["ticker"] for position in open_positions or []}
    diagnostics = {}
    for ticker in universe:
        results = outputs.get(ticker, {})
        missing = [name for name in live_strategy_names() if
                   results.get(name) is None or results[name].get("Signal") not in {"BUY", "WAIT", "AVOID"}]
        if missing:
            diagnostics[ticker] = {
                "status": "UNAVAILABLE", "signal": None, "score": None,
                "reason": "Incomplete dated strategy output: " + ", ".join(missing) + ".",
            }
            continue
        buys = [name for name, result in results.items() if result["Signal"] == "BUY"]
        avoids = [name for name, result in results.items() if result["Signal"] == "AVOID"]
        causes = []
        if ticker in held_tickers and len(avoids) >= 2:
            causes.append("Existing holding has two or more AVOID votes; review exit before adding")
        if not preferred:
            causes.append("No available regime-preferred long strategy")
        if len(buys) < minimum:
            causes.append(f"{len(buys)} BUY votes, below the {minimum}-vote minimum")
        if len(avoids) > MAX_CONSENSUS_AVOIDS:
            causes.append(f"{len(avoids)} AVOID votes, above the {MAX_CONSENSUS_AVOIDS}-vote maximum")
        if preferred and not set(buys) & preferred:
            causes.append("No BUY vote from a regime-preferred strategy")
        diagnostics[ticker] = {
            "status": "QUALIFIED" if ticker in qualified else "NOT_SELECTED",
            "signal": "BUY" if ticker in qualified else None,
            "score": next((row["Score"] for row in ranked if row["Ticker"] == ticker), None),
            "reason": "Qualified for allocation." if ticker in qualified else "; ".join(causes) + ".",
        }
    return diagnostics


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
    target_position_value=None,
    whole_shares=False,
):
    """Pure plan builder: no I/O and no ledger mutations."""
    resolved = resolve_plan_strategy(mode, regime_result, get_strategy_fn=get_strategy_fn)
    definition = resolved["definition"]
    quotes = {}
    ranked = []
    unavailable_universe = []
    candidate_diagnostics = {}

    if mode == CONSENSUS_MODE:
        ranked, unavailable_universe, consensus_outputs = _consensus_scan(
            universe, open_positions, price_data, resolved["recommendation"], get_strategy_fn
        )
        candidate_diagnostics = _consensus_candidate_diagnostics(
            universe, consensus_outputs, ranked, resolved["recommendation"], open_positions
        )
        for ticker in list(dict.fromkeys([*universe, *(p["ticker"] for p in open_positions or [])])):
            quote = _quote_for(ticker, price_data)
            if quote is not None:
                quotes[ticker] = quote
    elif definition is not None:
        scored = []
        for ticker in universe:
            result = analyze_ticker(definition, ticker, price_data)
            quote = _quote_for(ticker, price_data)
            if quote is not None:
                quotes[ticker] = quote
            if result is None:
                if ticker not in extra_strategy_benchmark_tickers() and ticker not in ("SPY", "QQQ"):
                    unavailable_universe.append(ticker)
                candidate_diagnostics[ticker] = {
                    "status": "UNAVAILABLE", "signal": None, "score": None,
                    "reason": "Strategy output is unavailable for the dated stock or benchmark history.",
                }
                continue
            candidate_diagnostics[ticker] = {
                "status": "QUALIFIED" if result.get("Signal") == "BUY" else "NOT_SELECTED",
                "signal": result.get("Signal"), "score": result.get("Score"),
                "reason": str(result.get("Reason") or "No strategy reason was supplied."),
            }
            if result.get("Signal") != "BUY":
                continue
            scored.append(result)
        scored.sort(key=definition.rank_key, reverse=True)
        ranked = scored
    else:
        candidate_diagnostics = {
            ticker: {
                "status": "NOT_SELECTED", "signal": None, "score": None,
                "reason": "Automatic mode has no available preferred long strategy in this regime.",
            }
            for ticker in universe
        }

    for position in open_positions or []:
        ticker = position.get("ticker")
        if ticker and ticker not in quotes:
            quote = _quote_for(ticker, price_data)
            if quote is not None:
                quotes[ticker] = quote

    market_reference_quotes = [
        _quote_for(ticker, price_data) for ticker in ("SPY", "QQQ")
    ]
    market_reference_dates = [
        quote["as_of"] for quote in market_reference_quotes if quote is not None
    ]
    if not market_reference_dates:
        market_reference_dates = [quote["as_of"] for quote in quotes.values()]
    reference_quote_as_of = max(market_reference_dates, default=None)

    allocation = allocate_buy_candidates(
        ranked,
        account_state,
        open_positions,
        quotes,
        min_stock_price=min_stock_price,
        max_position_value=max_position_value,
        target_position_value=target_position_value,
        whole_shares=whole_shares,
        reference_quote_as_of=reference_quote_as_of,
    )
    for row in allocation["buys"]:
        candidate_diagnostics[row["Ticker"]]["status"] = "RECOMMENDED"
    for row in allocation["skipped"]:
        ticker = row.get("Ticker")
        if ticker in candidate_diagnostics:
            candidate_diagnostics[ticker]["status"] = "ALLOCATION_BLOCKED"
            candidate_diagnostics[ticker]["reason"] = str(row.get("Reason") or "Allocation blocked.")
    if allocation["capacity_error"]:
        for row in ranked:
            candidate_diagnostics[row["Ticker"]]["status"] = "ALLOCATION_BLOCKED"
            candidate_diagnostics[row["Ticker"]]["reason"] = allocation["capacity_error"]
    holdings = (
        _review_consensus_holdings(open_positions, consensus_outputs, price_data)
        if mode == CONSENSUS_MODE else review_holdings(open_positions, definition, price_data)
    )
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
        "no_purchase_explanation": _no_purchase_explanation(resolved, allocation, ranked, min_stock_price),
        "unavailable_universe": unavailable_universe,
        "ranked_buy_count": len(ranked),
        "candidate_diagnostics": candidate_diagnostics,
        "reference_quote_as_of": reference_quote_as_of,
        "target_position_value": target_position_value,
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
    target_position_value=None,
    whole_shares=False,
):
    """Load required history, optionally classify the regime, then build a plan."""
    if load_market_data_fn is None:
        from data.market_data import load_market_data as load_market_data_fn
        load_market_data_fn = partial(
            load_market_data_fn, max_workers=LIVE_SCANNER_PRICE_WORKERS,
            live_failure_retry=True,
        )

    universe = list(universe if universe is not None else STOCK_UNIVERSE)
    as_of = (
        pd.Timestamp(as_of_date).normalize()
        if as_of_date is not None
        else pd.Timestamp.today().normalize()
    )
    regime_result = None
    started = perf_counter()
    stage_timings = {}
    include_regime = mode in (AUTOMATIC_MODE, CONSENSUS_MODE)
    definition = None
    if not include_regime:
        definition = get_strategy_fn(mode)

    if include_regime:
        if status_callback:
            status_callback("Detecting market regime", "SPY/QQQ")
        from market.regime import LIVE_LOOKBACK_CALENDAR_DAYS

        start = as_of - pd.Timedelta(days=LIVE_LOOKBACK_CALENDAR_DAYS)
        regime_data, _info = load_market_data_fn(
            ["SPY", "QQQ"], start, as_of, status_callback=status_callback
        )
        if as_of_date is None:
            observed_at = datetime.now(timezone.utc)
            regime_data = {
                ticker: completed_daily_close_frame(frame, observed_at)
                for ticker, frame in regime_data.items()
            }
        spy = regime_data.get("SPY")
        qqq = regime_data.get("QQQ")
        if spy is None or spy.empty or qqq is None or qqq.empty:
            raise ValueError(
                "Could not load sufficient SPY/QQQ data to classify the market regime."
            )
        regime_result = classify_market_regime(compute_market_features(spy, qqq))
        stage_timings["regime_seconds"] = round(perf_counter() - started, 3)
        if mode == AUTOMATIC_MODE:
            resolved = resolve_plan_strategy(
                AUTOMATIC_MODE, regime_result, get_strategy_fn=get_strategy_fn
            )
            definition = resolved["definition"]

    history_days = _history_requirement(definition, include_regime)
    if mode == CONSENSUS_MODE:
        history_days = max(
            history_days,
            *(get_strategy_fn(name).required_history_days for name in live_strategy_names()),
        )
    start_date = as_of - pd.Timedelta(days=calendar_days_for_trading_days(history_days))
    tickers = scan_tickers_for_plan(
        universe, open_positions, definition, include_regime=include_regime
    )
    if status_callback:
        status_callback("Loading price history", f"{len(tickers)} tickers")
    download_started = perf_counter()
    price_data, cache_info = load_market_data_fn(
        tickers, start_date, as_of, status_callback=status_callback
    )
    if as_of_date is None:
        observed_at = datetime.now(timezone.utc)
        price_data = {
            ticker: completed_daily_close_frame(frame, observed_at)
            for ticker, frame in price_data.items()
        }
    stage_timings["price_history_seconds"] = round(perf_counter() - download_started, 3)
    market_quotes = [
        quote for ticker in tickers
        if (quote := _quote_for(ticker, price_data)) is not None
    ]
    if include_regime and regime_result is None:
        raise ValueError("Automatic mode did not produce a regime classification.")

    if status_callback:
        status_callback("Building trading plan", mode if mode == CONSENSUS_MODE else definition.name if definition else "no strategy")
    scoring_started = perf_counter()
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
        target_position_value=target_position_value,
        whole_shares=whole_shares,
    )
    stage_timings["strategy_and_allocation_seconds"] = round(perf_counter() - scoring_started, 3)
    plan["cache_info"] = cache_info
    plan["scan_coverage"] = summarize_scan_coverage(universe, price_data, plan, min_stock_price)
    stage_timings["total_seconds"] = round(perf_counter() - started, 3)
    plan["scan_stage_timings"] = stage_timings
    if status_callback:
        status_callback("Scan complete", f"{plan['scan_coverage']['eligible']}/{len(universe)} rankable in {stage_timings['total_seconds']:.1f}s")
    plan["requested_as_of"] = str(as_of.date())
    plan["history_start"] = str(start_date.date())
    session_start, session_end = _price_session_bounds(plan)
    plan["price_session_start"] = session_start
    plan["price_session_end"] = session_end
    plan["market_data_as_of"] = max(
        (quote["as_of"] for quote in market_quotes), default=None
    )
    plan["data_warnings"] = _plan_data_warnings(plan)
    plan["regime_result"] = regime_result
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
        if plan.get("mode") == CONSENSUS_MODE:
            warnings.append(
                f"{ticker}: one or more live strategies lacked dated stock or "
                "benchmark history; excluded from consensus, not treated as a sell."
            )
        else:
            warnings.append(f"{ticker}: price history unavailable; not treated as a sell.")
    for item in plan.get("skipped_buys") or []:
        warnings.append(f"{item.get('Ticker')}: {item.get('Reason')}")
    reference = plan.get("reference_quote_as_of")
    if reference:
        reference_session = pd.Timestamp(reference).date()
        for row in plan.get("holdings") or []:
            raw = row.get("Price As Of")
            if raw and pd.Timestamp(raw).date() < reference_session:
                warnings.append(
                    f"{row.get('ticker')}: holding quote is dated {pd.Timestamp(raw).date()}, "
                    f"before market reference session {reference_session}; valuation may be stale."
                )
    cache = plan.get("cache_info") or {}
    for failure in cache.get("Data Source Failures") or []:
        warnings.append(
            f"{failure.get('Ticker')}: {failure.get('Reason')} "
            f"({failure.get('Requested Start')} to {failure.get('Requested End')})"
        )
    return warnings
