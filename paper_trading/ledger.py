"""Centralized paper-ledger reconciliation.

Checks use a single in-memory snapshot of account state, open positions,
and closed trades. They never write, repair, or reset stored rows.

Statuses are kept distinct on purpose:

    PASS           - check ran and matched
    FAIL           - stored ledger is internally inconsistent
    UNAVAILABLE    - check needs market prices that are missing/invalid
    MARKET_BREACH  - identities hold, but gross exposure exceeds starting capital
    INSOLVENCY     - identities hold, but equity is negative (e.g. a large
                     short loss). This is not an accounting error and must
                     not block a legitimate close.
"""

import math

from paper_trading.portfolio import (
    compute_exposure_summary,
    compute_position_metrics,
    is_valid_market_price,
    position_notional_exposure,
)


PASS = "PASS"
FAIL = "FAIL"
UNAVAILABLE = "UNAVAILABLE"
MARKET_BREACH = "MARKET_BREACH"
INSOLVENCY = "INSOLVENCY"
OK = "OK"

LEDGER_TOLERANCE = 1e-6
VALID_DIRECTIONS = ("LONG", "SHORT")


class AccountingError(ValueError):
    """The stored ledger would be internally inconsistent if this mutation committed."""


def _tolerance(*values):
    magnitude = max((abs(float(value)) for value in values if value is not None), default=1.0)
    return LEDGER_TOLERANCE * max(1.0, magnitude)


def _is_finite_number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number)


def _closed_pnl(trade):
    if trade["direction"] == "LONG":
        return (trade["exit_price"] - trade["entry_price"]) * trade["quantity"]
    return (trade["entry_price"] - trade["exit_price"]) * trade["quantity"]


def _check(name, status, detail=""):
    return {"Name": name, "Status": status, "Detail": detail}


def _allocation_matches(row, prefix):
    if not all(
        _is_finite_number(row.get(key))
        for key in ("entry_price", "quantity", "allocated_capital")
    ):
        return False, f"{prefix} has a non-finite price, quantity, or allocation."
    implied = float(row["entry_price"]) * float(row["quantity"])
    allocated = float(row["allocated_capital"])
    if abs(implied - allocated) > _tolerance(implied, allocated):
        return (
            False,
            f"{prefix}: allocated ${allocated:,.4f} != entry * quantity ${implied:,.4f}.",
        )
    return True, f"{prefix}: allocation matches entry * quantity."


def reconcile_ledger(account_state, open_positions, closed_trades, current_prices=None):
    """Return a structured audit of stored vs derived paper-accounting numbers."""
    checks = []
    current_prices = current_prices or {}
    open_positions = list(open_positions or [])
    closed_trades = list(closed_trades or [])

    cash = account_state.get("Cash")
    realized = account_state.get("Realized P&L")
    starting = account_state.get("Starting Capital")

    for name, value in (
        ("cash", cash),
        ("realized P&L", realized),
    ):
        if not _is_finite_number(value):
            checks.append(_check(f"Finite {name}", FAIL, f"{name} is missing or non-finite."))
        else:
            checks.append(_check(f"Finite {name}", PASS, f"{name}={float(value):,.4f}"))

    if starting is None or not _is_finite_number(starting) or float(starting) <= 0:
        checks.append(
            _check(
                "Starting capital",
                FAIL,
                "Starting capital is missing, non-finite, or not positive.",
            )
        )
        starting_ok = False
    else:
        starting = float(starting)
        checks.append(_check("Starting capital", PASS, f"${starting:,.2f}"))
        starting_ok = True

    row_errors = []
    for position in open_positions:
        label = f"open #{position.get('id')} {position.get('ticker')}"
        if position.get("direction") not in VALID_DIRECTIONS:
            row_errors.append(f"{label}: invalid direction {position.get('direction')!r}.")
        for field in ("entry_price", "quantity", "allocated_capital"):
            value = position.get(field)
            if not _is_finite_number(value) or float(value) <= 0:
                row_errors.append(f"{label}: {field} must be a finite positive number.")
        ok, detail = _allocation_matches(position, label)
        if not ok:
            row_errors.append(detail)

    for trade in closed_trades:
        label = f"closed #{trade.get('id')} {trade.get('ticker')}"
        if trade.get("direction") not in VALID_DIRECTIONS:
            row_errors.append(f"{label}: invalid direction {trade.get('direction')!r}.")
        for field in ("entry_price", "exit_price", "quantity", "allocated_capital"):
            value = trade.get(field)
            if not _is_finite_number(value) or float(value) <= 0:
                row_errors.append(f"{label}: {field} must be a finite positive number.")
        ok, detail = _allocation_matches(trade, label)
        if not ok:
            row_errors.append(detail)
        if _is_finite_number(trade.get("realized_pnl")) and all(
            _is_finite_number(trade.get(key))
            for key in ("entry_price", "exit_price", "quantity")
        ):
            expected = _closed_pnl(trade)
            stored = float(trade["realized_pnl"])
            if abs(expected - stored) > _tolerance(expected, stored):
                row_errors.append(
                    f"{label}: stored realized P&L ${stored:,.4f} != "
                    f"formula ${expected:,.4f}."
                )

    if row_errors:
        checks.append(_check("Row integrity", FAIL, " ".join(row_errors)))
    else:
        checks.append(_check("Row integrity", PASS, "Directions, quantities, prices, and allocations are valid."))

    accounting_ready = (
        starting_ok
        and _is_finite_number(cash)
        and _is_finite_number(realized)
        and not row_errors
    )
    cash = float(cash) if _is_finite_number(cash) else None
    realized = float(realized) if _is_finite_number(realized) else None

    if accounting_ready:
        open_allocated = sum(float(position["allocated_capital"]) for position in open_positions)
        left = cash + open_allocated
        right = starting + realized
        if abs(left - right) <= _tolerance(left, right):
            checks.append(
                _check(
                    "Cash identity",
                    PASS,
                    f"cash ${cash:,.4f} + open allocated ${open_allocated:,.4f} "
                    f"= starting ${starting:,.4f} + realized P&L ${realized:,.4f}.",
                )
            )
        else:
            checks.append(
                _check(
                    "Cash identity",
                    FAIL,
                    f"cash ${cash:,.4f} + open allocated ${open_allocated:,.4f} "
                    f"= ${left:,.4f} but starting + realized P&L = ${right:,.4f}.",
                )
            )

        closed_sum = sum(float(trade["realized_pnl"]) for trade in closed_trades)
        if abs(closed_sum - realized) <= _tolerance(closed_sum, realized):
            checks.append(
                _check(
                    "Realized P&L identity",
                    PASS,
                    f"account realized P&L ${realized:,.4f} = sum of closed trades "
                    f"${closed_sum:,.4f}.",
                )
            )
        else:
            checks.append(
                _check(
                    "Realized P&L identity",
                    FAIL,
                    f"account realized P&L ${realized:,.4f} != sum of closed trades "
                    f"${closed_sum:,.4f}.",
                )
            )
    else:
        checks.append(
            _check(
                "Cash identity",
                FAIL if (cash is None or realized is None or not starting_ok or row_errors) else UNAVAILABLE,
                "Skipped because cash, starting capital, or row integrity failed.",
            )
        )
        checks.append(
            _check(
                "Realized P&L identity",
                FAIL if (realized is None or row_errors) else UNAVAILABLE,
                "Skipped because realized P&L or row integrity failed.",
            )
        )

    valuation_status = UNAVAILABLE
    solvency_status = OK
    equity = None
    exposure = None
    missing_prices = [
        position["ticker"]
        for position in open_positions
        if not is_valid_market_price(current_prices.get(position["ticker"]))
    ]

    if missing_prices:
        checks.append(
            _check(
                "Valuation snapshot",
                UNAVAILABLE,
                "Missing or invalid current prices for "
                + ", ".join(sorted(set(missing_prices)))
                + ". Valuation checks are not PASS.",
            )
        )
        checks.append(_check("Equity identities", UNAVAILABLE, "Requires a complete current-price snapshot."))
        checks.append(_check("Gross/net exposure", UNAVAILABLE, "Requires a complete current-price snapshot."))
    elif not accounting_ready:
        checks.append(_check("Valuation snapshot", UNAVAILABLE, "Accounting snapshot is not usable."))
        checks.append(_check("Equity identities", UNAVAILABLE, "Accounting snapshot is not usable."))
        checks.append(_check("Gross/net exposure", UNAVAILABLE, "Accounting snapshot is not usable."))
    else:
        long_market_value = 0.0
        short_reserved = 0.0
        short_unrealized = 0.0
        total_unrealized = 0.0
        for position in open_positions:
            price = float(current_prices[position["ticker"]])
            metrics = compute_position_metrics(position, price)
            total_unrealized += metrics["Unrealized P&L"]
            if position["direction"] == "LONG":
                long_market_value += metrics["Market Value"]
            else:
                short_reserved += float(position["allocated_capital"])
                short_unrealized += metrics["Unrealized P&L"]

        equity_from_parts = cash + long_market_value + short_reserved + short_unrealized
        equity_from_pnl = starting + realized + total_unrealized
        equity = equity_from_parts
        if abs(equity_from_parts - equity_from_pnl) <= _tolerance(equity_from_parts, equity_from_pnl):
            checks.append(
                _check(
                    "Equity identities",
                    PASS,
                    f"cash + long MV + short reserved + short uPnL = ${equity_from_parts:,.4f}; "
                    f"starting + realized + unrealized = ${equity_from_pnl:,.4f}.",
                )
            )
        else:
            checks.append(
                _check(
                    "Equity identities",
                    FAIL,
                    f"cash + long MV + short reserved + short uPnL = ${equity_from_parts:,.4f} "
                    f"but starting + realized + unrealized = ${equity_from_pnl:,.4f}.",
                )
            )

        try:
            exposure = compute_exposure_summary(open_positions, current_prices, starting)
            gross = exposure["Gross Exposure"]
            net = exposure["Net Exposure"]
            remaining = exposure["Remaining Capacity"]
            expected_net = exposure["Current Long Exposure"] - exposure["Current Short Exposure"]
            expected_remaining = max(0.0, starting - gross)
            if (
                abs(net - expected_net) <= _tolerance(net, expected_net)
                and abs(remaining - expected_remaining) <= _tolerance(remaining, expected_remaining)
            ):
                detail = (
                    f"gross ${gross:,.2f}, net ${net:,.2f}, remaining ${remaining:,.2f}."
                )
                if gross > starting + _tolerance(gross, starting):
                    valuation_status = MARKET_BREACH
                    checks.append(
                        _check(
                            "Gross/net exposure",
                            MARKET_BREACH,
                            detail
                            + " Gross exposure exceeds starting capital; this is a "
                            "market move, not an accounting error, and does not force a close.",
                        )
                    )
                else:
                    valuation_status = PASS
                    checks.append(_check("Gross/net exposure", PASS, detail))
            else:
                checks.append(
                    _check(
                        "Gross/net exposure",
                        FAIL,
                        f"net ${net:,.4f} vs {expected_net:,.4f}; remaining "
                        f"${remaining:,.4f} vs {expected_remaining:,.4f}.",
                    )
                )
                valuation_status = FAIL
        except ValueError as error:
            checks.append(_check("Gross/net exposure", UNAVAILABLE, str(error)))
            valuation_status = UNAVAILABLE

        if equity is not None and equity < -_tolerance(equity):
            solvency_status = INSOLVENCY
            checks.append(
                _check(
                    "Solvency",
                    INSOLVENCY,
                    f"Equity is ${equity:,.2f}. Large short losses can make cash/equity "
                    "negative; that is not an accounting error and must not block a close.",
                )
            )
        else:
            checks.append(_check("Solvency", PASS, f"Equity is ${equity:,.2f}."))

    accounting_fail = any(check["Status"] == FAIL for check in checks)
    overall = FAIL if accounting_fail else PASS
    if not accounting_fail:
        if valuation_status == MARKET_BREACH:
            overall = MARKET_BREACH
        elif solvency_status == INSOLVENCY:
            overall = INSOLVENCY
        elif valuation_status == UNAVAILABLE:
            overall = UNAVAILABLE

    return {
        "Status": overall,
        "Accounting": FAIL if accounting_fail else PASS,
        "Valuation": valuation_status,
        "Solvency": solvency_status,
        "Checks": checks,
        "Equity": equity,
        "Exposure": exposure,
    }


def assert_accounting_consistent(account_state, open_positions, closed_trades):
    """Raise AccountingError if stored identities would be wrong.

    Intentionally ignores missing market prices, market-driven exposure
    breaches, and insolvency after large short losses.
    """
    report = reconcile_ledger(account_state, open_positions, closed_trades, current_prices=None)
    failures = [check for check in report["Checks"] if check["Status"] == FAIL]
    if failures:
        details = " ".join(check["Detail"] or check["Name"] for check in failures)
        raise AccountingError("Paper ledger accounting check failed: " + details)
    return report
