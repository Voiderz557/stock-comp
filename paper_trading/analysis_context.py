"""Read-only snapshot of the paper-trading assistant for AI analysis.

This module only composes existing SELECT-style helpers. It does not call
initialize_storage, ensure_plan_schema, save_plan, or any trade function.
It does not import the AI provider layer.
"""

from __future__ import annotations

from datetime import datetime, timezone

from config import MAX_POSITION_VALUE, MIN_STOCK_PRICE
from paper_trading import storage
from paper_trading.plan_store import (
    DROPPED_CANDIDATE_NOTE,
    compare_plans,
    describe_plan_age,
    detect_plan_staleness,
    list_saved_plans,
    load_latest_plan,
    previous_plan,
)
from paper_trading.portfolio import compute_exposure_summary, is_valid_market_price
from paper_trading.prices import SOURCE_LABEL


RECENT_CLOSED_TRADE_LIMIT = 20
PLAN_HISTORY_LIMIT = 10
MAX_UNTRUSTED_CHARS = 400
MAX_CONTEXT_CHARS = 60_000
MISSING_AFTER_RELOAD = (
    "skipped_buys, unavailable_universe, ranked_buy_count, and the regime "
    "recommendation object are not persisted; after reload only data_warnings "
    "strings remain."
)


def _utc_now(now=None):
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.isoformat()


def _untrusted_text(value):
    if value is None:
        return None
    text = str(value)
    return {
        "text": text[:MAX_UNTRUSTED_CHARS],
        "truncated": len(text) > MAX_UNTRUSTED_CHARS,
        "untrusted": True,
        "layer": "untrusted_note",
    }


def _finite_or_none(value):
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _quote_for(ticker, current_prices, price_quotes):
    quotes = price_quotes or {}
    prices = current_prices or {}
    quote = quotes.get(ticker) or {}
    price = quote.get("price", prices.get(ticker))
    if not is_valid_market_price(price):
        return None
    return {
        "price": float(price),
        "as_of": quote.get("as_of"),
        "source": quote.get("source_label") or SOURCE_LABEL,
    }


def _position_valuation(position, current_prices, price_quotes):
    quote = _quote_for(position["ticker"], current_prices, price_quotes)
    entry = _finite_or_none(position.get("entry_price"))
    quantity = _finite_or_none(position.get("quantity"))
    allocated = _finite_or_none(position.get("allocated_capital"))
    direction = position.get("direction")
    if quote is None:
        return {
            "current_price": None,
            "price_as_of": None,
            "market_value": None,
            "unrealized_pnl": None,
            "current_exposure": None,
            "valuation_available": False,
            "valuation_note": (
                "No valid current daily close. Market value, exposure, and "
                "unrealized P&L are null and are not estimated from entry price."
            ),
        }

    price = quote["price"]
    if direction == "LONG":
        market_value = price * quantity if quantity is not None else None
        unrealized = (
            (price - entry) * quantity
            if entry is not None and quantity is not None
            else None
        )
        exposure = price * quantity if quantity is not None else None
    else:
        unrealized = (
            (entry - price) * quantity
            if entry is not None and quantity is not None
            else None
        )
        market_value = (
            allocated + unrealized
            if allocated is not None and unrealized is not None
            else None
        )
        exposure = abs(price * quantity) if quantity is not None else None
    return {
        "current_price": price,
        "price_as_of": quote["as_of"],
        "market_value": market_value,
        "unrealized_pnl": unrealized,
        "current_exposure": exposure,
        "valuation_available": True,
        "valuation_note": None,
    }


def _current_positions(open_positions, current_prices, price_quotes):
    rows = []
    missing = []
    for position in open_positions or []:
        valuation = _position_valuation(position, current_prices, price_quotes)
        if not valuation["valuation_available"]:
            missing.append(position.get("ticker"))
        rows.append(
            {
                "position_id": position.get("id"),
                "ticker": position.get("ticker"),
                "direction": position.get("direction"),
                "entry_price": _finite_or_none(position.get("entry_price")),
                "quantity": _finite_or_none(position.get("quantity")),
                "allocated_capital": _finite_or_none(position.get("allocated_capital")),
                "entry_timestamp": position.get("entry_timestamp"),
                "strategy": position.get("strategy"),
                "reason": _untrusted_text(position.get("reason")),
                **valuation,
            }
        )
    return rows, missing


def _exposure_block(open_positions, current_prices, starting_capital):
    try:
        summary = compute_exposure_summary(
            open_positions, current_prices or {}, starting_capital
        )
    except ValueError as error:
        return {
            "current_long_exposure": None,
            "current_short_exposure": None,
            "gross_exposure": None,
            "net_exposure": None,
            "remaining_capacity": None,
            "available": False,
            "unavailable_reason": str(error),
        }
    return {
        "current_long_exposure": summary["Current Long Exposure"],
        "current_short_exposure": summary["Current Short Exposure"],
        "gross_exposure": summary["Gross Exposure"],
        "net_exposure": summary["Net Exposure"],
        "remaining_capacity": summary["Remaining Capacity"],
        "available": True,
        "unavailable_reason": None,
    }


def _slim_recommendation(row, section):
    if section == "BUY":
        ticker = row.get("Ticker")
        action = "BUY"
        signal = row.get("Signal")
    else:
        ticker = row.get("ticker")
        action = row.get("Action")
        signal = row.get("Signal")
    return {
        "recommendation_id": row.get("recommendation_id"),
        "section": section,
        "ticker": ticker,
        "action": action,
        "signal": signal,
        "score": row.get("Score"),
        "price": row.get("Price"),
        "price_as_of": row.get("Price As Of"),
        "quantity": row.get("Quantity") if section == "BUY" else row.get("quantity"),
        "allocation": row.get("Allocation") if section == "BUY" else row.get("allocated_capital"),
        "position_id": row.get("id") if section != "BUY" else None,
        "direction": row.get("direction") if section != "BUY" else None,
        "status": row.get("status"),
        "executed_trade_kind": row.get("executed_trade_kind"),
        "executed_trade_id": row.get("executed_trade_id"),
        "dismissal_note": _untrusted_text(row.get("dismissal_note")),
        "reason": _untrusted_text(row.get("Reason")),
        "layer": "algorithm_signal",
    }


def _plan_block(plan):
    if plan is None:
        return None
    buys = [_slim_recommendation(row, "BUY") for row in plan.get("buys") or []]
    holdings = [_slim_recommendation(row, "HOLDING") for row in plan.get("holdings") or []]
    snapshot = plan.get("account_snapshot") or {}
    return {
        "id": plan.get("id"),
        "generated_at": plan.get("generated_at"),
        "age": describe_plan_age(plan) if plan.get("generated_at") else None,
        "price_session_start": plan.get("price_session_start"),
        "price_session_end": plan.get("price_session_end"),
        "price_source": plan.get("price_source") or SOURCE_LABEL,
        "mode": plan.get("mode"),
        "strategy_name": plan.get("strategy_name"),
        "regime_name": plan.get("regime_name"),
        "strategy_reason": _untrusted_text(plan.get("strategy_reason")),
        "no_purchase_explanation": plan.get("no_purchase_explanation"),
        "needs_regeneration": bool(plan.get("needs_regeneration")),
        "stale_reason": plan.get("stale_reason"),
        "data_warnings": list(plan.get("data_warnings") or []),
        "buys": buys,
        "holdings": holdings,
        "generation_portfolio": {
            "note": (
                "Ledger snapshot used when this plan was generated. "
                "It is not the current portfolio."
            ),
            "cash": snapshot.get("Cash"),
            "starting_capital": snapshot.get("Starting Capital"),
            "realized_pnl": snapshot.get("Realized P&L"),
            "open_position_ids": list(snapshot.get("Open Position IDs") or []),
        },
        "cash_before": plan.get("cash_before"),
        "cash_after": plan.get("cash_after"),
        "remaining_capacity_before": plan.get("remaining_capacity_before"),
        "remaining_capacity_after": plan.get("remaining_capacity_after"),
    }


def _closed_trade_row(trade):
    return {
        "ticker": trade.get("ticker"),
        "direction": trade.get("direction"),
        "entry_price": _finite_or_none(trade.get("entry_price")),
        "exit_price": _finite_or_none(trade.get("exit_price")),
        "quantity": _finite_or_none(trade.get("quantity")),
        "allocated_capital": _finite_or_none(trade.get("allocated_capital")),
        "realized_pnl": _finite_or_none(trade.get("realized_pnl")),
        "entry_timestamp": trade.get("entry_timestamp"),
        "exit_timestamp": trade.get("exit_timestamp"),
        "strategy": trade.get("strategy"),
        "reason": _untrusted_text(trade.get("reason")),
    }


def _trim_context(context):
    import json

    encoded = json.dumps(context, default=str)
    if len(encoded) <= MAX_CONTEXT_CHARS:
        context["size"] = {"characters": len(encoded), "trimmed": False}
        return context
    context["recent_closed_trades"] = []
    context["plan_history"] = (context.get("plan_history") or [])[:3]
    context["history_coverage"]["trimmed_for_size"] = True
    encoded = json.dumps(context, default=str)
    context["size"] = {
        "characters": len(encoded),
        "trimmed": True,
        "limit": MAX_CONTEXT_CHARS,
    }
    return context


def build_analysis_context(
    db_path=None,
    current_prices=None,
    price_quotes=None,
    now=None,
    recent_closed_limit=RECENT_CLOSED_TRADE_LIMIT,
):
    """Return a JSON-ready snapshot. Never writes to the paper ledger."""
    snapshot_at = _utc_now(now)
    account = storage.get_account_state(db_path)
    open_positions = storage.get_open_positions(db_path)
    closed_trades = storage.get_closed_trades(db_path)
    plan = load_latest_plan(db_path)
    prior = previous_plan(plan["id"], db_path=db_path) if plan else None
    plan_index = list_saved_plans(db_path)
    stale = False
    stale_reason = None
    if plan is not None:
        stale, stale_reason = detect_plan_staleness(plan, account, open_positions)

    positions, missing_prices = _current_positions(
        open_positions, current_prices, price_quotes
    )
    exposure = _exposure_block(open_positions, current_prices, account["Starting Capital"])
    valued = [row for row in positions if row["valuation_available"]]
    totals_complete = not missing_prices
    portfolio_value = None
    total_unrealized = None
    if totals_complete:
        total_unrealized = sum((row["unrealized_pnl"] or 0.0) for row in valued)
        market_value = sum((row["market_value"] or 0.0) for row in valued)
        portfolio_value = account["Cash"] + market_value
    else:
        totals_note = (
            "Portfolio value and unrealized P&L are null because a current "
            "daily close is missing for: "
            + ", ".join(sorted(set(missing_prices)))
            + ". Totals are not filled with entry-price estimates."
        )

    recent = [_closed_trade_row(trade) for trade in (closed_trades or [])[:recent_closed_limit]]
    audit = storage.audit_ledger(current_prices, db_path=db_path)
    warnings = []
    if stale:
        warnings.append(
            {
                "code": "stale_plan",
                "message": stale_reason or "The current plan needs to be regenerated.",
            }
        )
    if plan and plan.get("needs_regeneration"):
        warnings.append(
            {
                "code": "needs_regeneration",
                "message": plan.get("stale_reason") or "A recorded action requires a new plan.",
            }
        )
    if not exposure["available"]:
        warnings.append(
            {
                "code": "capacity_unavailable",
                "message": exposure["unavailable_reason"],
            }
        )
    for item in (plan or {}).get("data_warnings") or []:
        warnings.append({"code": "plan_data", "message": item})
    if audit.get("Accounting") not in (None, "PASS"):
        warnings.append(
            {
                "code": "ledger_accounting",
                "message": f"Accounting={audit.get('Accounting')}",
            }
        )
    if audit.get("Valuation") == "UNAVAILABLE":
        warnings.append(
            {
                "code": "ledger_valuation_unavailable",
                "message": "Ledger valuation checks need a complete daily-close snapshot.",
            }
        )
    if audit.get("Status") == "MARKET_BREACH":
        warnings.append(
            {
                "code": "market_breach",
                "message": "Gross exposure is above starting capital because prices moved.",
            }
        )

    context = {
        "role": "read_only_advisory_snapshot",
        "mutation_tools": [],
        "layers": {
            "recorded_facts": "Ledger cash, positions, closed trades, and saved plan rows.",
            "algorithm_signals": "Strategy Signal/Score/Action on the saved plan.",
            "ai_interpretation": "Not part of this snapshot. Produced only after Analyze.",
        },
        "times": {
            "snapshot_at": snapshot_at,
            "plan_generated_at": None if plan is None else plan.get("generated_at"),
            "plan_price_session_start": None if plan is None else plan.get("price_session_start"),
            "plan_price_session_end": None if plan is None else plan.get("price_session_end"),
            "price_source": SOURCE_LABEL,
        },
        "constraints": {
            "min_stock_price": MIN_STOCK_PRICE,
            "max_position_value": MAX_POSITION_VALUE,
            "starting_capital": account["Starting Capital"],
            "duplicate_execution": "A recommendation can be recorded once; later attempts are duplicates.",
            "dropped_candidate_note": DROPPED_CANDIDATE_NOTE,
        },
        "current_portfolio": {
            "source": "live_ledger",
            "snapshot_at": snapshot_at,
            "cash": account["Cash"],
            "starting_capital": account["Starting Capital"],
            "realized_pnl": account["Realized P&L"],
            "portfolio_value": portfolio_value if totals_complete else None,
            "total_unrealized_pnl": total_unrealized if totals_complete else None,
            "totals_available": totals_complete,
            "totals_unavailable_reason": None if totals_complete else totals_note,
            "open_positions": positions,
            "exposure": exposure,
        },
        "current_plan": _plan_block(plan),
        "plan_generation_portfolio": None if plan is None else {
            "source": "saved_plan_account_snapshot",
            "note": (
                "Portfolio used to generate the current saved plan. "
                "Do not treat this as the live account."
            ),
            **(_plan_block(plan)["generation_portfolio"]),
        },
        "plan_diff": compare_plans(prior, plan),
        "plan_history": [
            {
                "id": item.get("id"),
                "generated_at": item.get("generated_at"),
                "strategy_name": item.get("strategy_name"),
                "regime_name": item.get("regime_name"),
                "needs_regeneration": bool(item.get("needs_regeneration")),
            }
            for item in plan_index[:PLAN_HISTORY_LIMIT]
        ],
        "recent_closed_trades": recent,
        "warnings": warnings,
        "history_coverage": {
            "complete_action_history": False,
            "includes": [
                f"up to {recent_closed_limit} most recent closed trades",
                "recommendation statuses on the current saved plan only",
                f"up to {PLAN_HISTORY_LIMIT} plan index rows",
            ],
            "missing": [
                "recommendation statuses from older plans",
                "dismissed or recorded actions that are not on the current plan",
                MISSING_AFTER_RELOAD,
                "any action that was never written to the ledger or plan tables",
            ],
            "closed_trade_total": len(closed_trades or []),
            "closed_trades_included": len(recent),
            "saved_plan_total": len(plan_index),
            "trimmed_for_size": False,
        },
        "ledger_audit": {
            "status": audit.get("Status"),
            "accounting": audit.get("Accounting"),
            "valuation": audit.get("Valuation"),
            "solvency": audit.get("Solvency"),
        },
    }
    if plan is None:
        context["warnings"].append(
            {
                "code": "no_saved_plan",
                "message": "No saved plan exists yet.",
            }
        )
    return _trim_context(context)
