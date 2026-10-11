"""Build cloud daily plans with the existing recommendation-only engine.

This adapter translates the public account snapshot into the shape expected by
``paper_trading.trading_plan``.  It never imports execution functions and never
opens, closes, or mutates paper positions.
"""

from __future__ import annotations

import math
from dataclasses import asdict, is_dataclass
from typing import Any

from config import MIN_STOCK_PRICE
from data.scanner_universe import broad_current_scanner_universe, expanded_current_scanner_universe
from market.strategy_selector import recommend_strategy
from paper_trading.trading_plan import (
    CONSENSUS_MODE,
    DEFAULT_TARGET_POSITION_VALUE,
    load_and_build_trading_plan,
)


def account_inputs(snapshot: dict) -> tuple[dict, list[dict]]:
    account = snapshot.get("account") or {}
    portfolio = snapshot.get("portfolio") or {}
    account_state = {
        "Starting Capital": float(account["starting_capital"]),
        "Cash": float(portfolio["cash"]),
        "Realized P&L": 0.0,
    }
    positions = []
    for raw in snapshot.get("positions") or []:
        quantity = float(raw["quantity"])
        average_price = raw.get("average_price")
        exact_basis = raw.get("exact_cost_basis")
        if average_price is not None:
            entry_price = float(average_price)
            allocated = entry_price * quantity
        elif exact_basis is not None:
            allocated = float(exact_basis)
            entry_price = allocated / quantity
        else:
            raise ValueError(f"{raw.get('ticker', 'Holding')} has no cost basis.")
        positions.append(
            {
                "id": raw.get("id"),
                "ticker": str(raw["ticker"]).upper(),
                "direction": str(raw["side"]).upper(),
                "quantity": quantity,
                "entry_price": entry_price,
                "allocated_capital": allocated,
                "strategy": "Imported account snapshot",
                "reason": "Existing competition holding supplied by the account owner.",
            }
        )
    return account_state, positions


def build_cloud_plan(
    snapshot: dict,
    strategy: str = CONSENSUS_MODE,
    target_position_value: float = DEFAULT_TARGET_POSITION_VALUE,
    minimum_price: float = MIN_STOCK_PRICE,
    universe_mode: str = "EXPANDED",
) -> dict:
    if not math.isfinite(minimum_price) or minimum_price < 0 or minimum_price > 1_000_000:
        raise ValueError("Minimum stock price must be between $0 and $1,000,000.")
    if universe_mode not in {"EXPANDED", "BROAD"}:
        raise ValueError("Universe mode must be EXPANDED or BROAD.")
    selected_universe = (
        expanded_current_scanner_universe()
        if universe_mode == "EXPANDED"
        else broad_current_scanner_universe()
    )
    account_state, positions = account_inputs(snapshot)
    plan = load_and_build_trading_plan(
        strategy,
        account_state,
        positions,
        universe=selected_universe,
        target_position_value=target_position_value,
        min_stock_price=minimum_price,
        whole_shares=True,
    )
    plan["user_minimum_price"] = minimum_price
    plan["universe_mode"] = universe_mode
    plan["universe_size"] = len(selected_universe)
    plan["research_only_price_filter"] = minimum_price < MIN_STOCK_PRICE
    return plan


def plan_payload(snapshot: dict, plan: dict) -> dict:
    """Return the auditable, JSON-safe payload accepted by the save-plan RPC."""
    recommendations: list[dict] = []
    warnings = list(plan.get("data_warnings") or [])
    if plan.get("research_only_price_filter"):
        warnings.append(
            "Research-only price filter: candidates under $5 are not eligible for competition paper BUY actions. "
            "Any proposed allocation to them is hypothetical, not available paper capacity."
        )
    if plan.get("universe_mode") == "BROAD":
        warnings.append("Broad watchlist is a current-day discovery pool, not verified historical index membership or competition eligibility. Review dated prices and unavailable symbols.")
    coverage = plan.get("scan_coverage")
    if coverage:
        elapsed = (plan.get("scan_stage_timings") or {}).get("total_seconds")
        warnings.append(
            f"Scan coverage: {coverage['scanned']} requested, {coverage['quoted']} quoted, "
            f"{coverage['stale']} stale, {coverage['missing']} missing, "
            f"{coverage['failed']} provider-history failures, "
            f"{coverage['eligible']} rankable at the selected price floor. "
            + (f"Completed in {elapsed:.1f}s. " if isinstance(elapsed, (int, float)) else "")
            + "Rankable does not mean BUY or competition eligible."
        )
        for issue in coverage.get("issues") or []:
            warnings.append(f"Scan audit {issue['ticker']}: {'; '.join(issue['reasons'])}.")
    seen: set[str] = set()

    def add(row: dict, action: str, signal: str) -> None:
        ticker = str(row.get("Ticker") or row.get("ticker") or "").upper()
        score = row.get("Score")
        if not ticker or ticker in seen:
            return
        if score is None:
            warnings.append(f"{ticker}: recommendation omitted because its score is unavailable.")
            return
        seen.add(ticker)
        recommendations.append(
            {
                "ticker": ticker,
                "company": ticker,
                "action": action,
                "score": float(score),
                "signal": signal,
                "reason": str(row.get("Reason") or "No explanation was supplied."),
                "factor_details": _json_safe({
                    **(row.get("Factor Details") or {}),
                    **({"Proposed Allocation": row["Allocation"]} if row.get("Allocation") is not None else {}),
                    **({"Proposed Quantity": row["Quantity"]} if row.get("Quantity") is not None else {}),
                    **({"Research Only — Below $5": True} if action == "BUY" and _optional_float(row.get("Price")) is not None and float(row["Price"]) < MIN_STOCK_PRICE else {}),
                }),
                "rank": len(recommendations) + 1,
                "last_price": _optional_float(row.get("Price")),
                "day_move_percent": None,
                "price_as_of": _timestamp_or_none(row.get("Price As Of")),
            }
        )

    for row in plan.get("buys") or []:
        add(row, "BUY", str(row.get("Signal") or "BUY"))
    for row in plan.get("holdings") or []:
        plan_action = row.get("Action")
        public_action = "SELL" if plan_action == "REVIEW EXIT" else "WAIT"
        add(row, public_action, str(row.get("Signal") or plan_action or "WAIT"))

    # The paper-action RPC requires a same-session saved quote for *every*
    # open holding. An unscored holding cannot be persisted as a recommendation
    # (score is NOT NULL), so make the resulting block explicit rather than
    # showing a BUY button that can only fail after confirmation.
    market_as_of = _timestamp_or_none(plan.get("market_data_as_of"))
    if market_as_of is None:
        raise ValueError("No dated market price is available; the plan was not saved as current.")
    saved_by_ticker = {item["ticker"]: item for item in recommendations}
    blockers = []
    for position in snapshot.get("positions") or []:
        ticker = str(position.get("ticker") or "").upper()
        if str(position.get("side") or "").upper() == "SHORT":
            blockers.append(f"{ticker}: open SHORT holdings require cash-proceeds reconciliation")
            continue
        saved = saved_by_ticker.get(ticker)
        if saved is None:
            blockers.append(f"{ticker}: no scored holding review was saved")
        elif saved["last_price"] is None or saved["last_price"] <= 0 or not saved["price_as_of"]:
            blockers.append(f"{ticker}: a dated holding quote is missing")
        elif saved["price_as_of"][:10] != market_as_of[:10]:
            blockers.append(f"{ticker}: holding quote is not from the plan's market session")
    if blockers:
        warnings.append("Paper actions unavailable: " + "; ".join(blockers) + ". Build a fresh plan after resolving this gap.")

    regime_result = plan.get("regime_result")
    regime = _regime_payload(regime_result)
    buy_count = sum(item["action"] == "BUY" for item in recommendations)
    sell_count = sum(item["action"] == "SELL" for item in recommendations)
    wait_count = sum(item["action"] == "WAIT" for item in recommendations)
    if recommendations:
        summary = f"{buy_count} buy, {sell_count} review-to-sell, and {wait_count} wait recommendation(s)."
    else:
        summary = str(plan.get("no_purchase_explanation") or "No actionable recommendations qualified today.")
    if coverage:
        summary = (
            f"{str(plan.get('universe_mode', 'EXPANDED')).title()} watchlist: "
            f"{coverage['eligible']}/{coverage['scanned']} rankable "
            f"({coverage['missing']} missing, {coverage['stale']} stale). {summary}"
        )

    return {
        "expected_portfolio_version": int(snapshot["account"]["portfolio_version"]),
        "strategy_name": str(plan.get("strategy_name") or "Cash / no preferred long strategy"),
        "market_as_of": market_as_of,
        "summary": summary,
        "portfolio_snapshot": {
            "cash": snapshot["portfolio"]["cash"],
            "positions": snapshot.get("positions") or [],
            "minimum_stock_price": plan.get("user_minimum_price", MIN_STOCK_PRICE),
            "universe_mode": plan.get("universe_mode", "EXPANDED"),
            "universe_size": plan.get("universe_size"),
            "scan_coverage": _json_safe(coverage) if coverage else None,
            "scan_stage_timings": _json_safe(plan.get("scan_stage_timings") or {}),
            "research_only_price_filter": bool(plan.get("research_only_price_filter")),
            "candidate_diagnostics": _json_safe(plan.get("candidate_diagnostics") or {}),
        },
        "regime": regime,
        "warnings": list(dict.fromkeys(str(item) for item in warnings if item)),
        "recommendations": recommendations,
    }


def _regime_payload(result: Any) -> dict:
    if result is None:
        return {
            "name": "UNAVAILABLE",
            "label": "Unavailable",
            "reason": "A market regime was not requested for this plan.",
            "trend_strength": None,
            "market_breadth": None,
            "volatility": None,
            "preferred_strategies": [],
        }
    recommendation = recommend_strategy(result)
    return {
        "name": result.regime,
        "label": result.regime.replace("_", " ").title(),
        "reason": result.reason,
        "trend_strength": round(float(result.confidence) * 100, 2),
        "market_breadth": None,
        "volatility": round(float(result.features.market_volatility_20d) * 100, 4),
        "preferred_strategies": list(recommendation.preferred_strategies),
        "supporting_metrics": _json_safe(result.supporting_metrics),
        "features": _json_safe(asdict(result.features) if is_dataclass(result.features) else vars(result.features)),
    }


def _optional_float(value: Any) -> float | None:
    return None if value is None else float(value)


def _timestamp_or_none(value: Any) -> str | None:
    if value is None or value == "":
        return None
    text = str(value)
    if len(text) == 10:
        return f"{text}T23:59:59Z"
    return text


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "item"):
        return value.item()
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value
