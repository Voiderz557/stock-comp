"""Manual competition-account snapshot. This is not a trade importer."""

from __future__ import annotations

from paper_trading import storage
from paper_trading.ledger import LEDGER_TOLERANCE
from paper_trading.storage import SNAPSHOT_REASON, SNAPSHOT_STRATEGY


class AccountSetupError(ValueError):
    """The entered snapshot is incomplete or would break ledger identity."""


def _finite(name, value, allow_zero=False):
    if value is None or value == "":
        raise AccountSetupError(f"{name} is required.")
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise AccountSetupError(f"{name} must be a number.") from error
    if number != number or number in (float("inf"), float("-inf")):
        raise AccountSetupError(f"{name} must be a finite number.")
    if allow_zero:
        if number < 0:
            raise AccountSetupError(f"{name} cannot be negative.")
    elif number <= 0:
        raise AccountSetupError(f"{name} must be a positive number.")
    return number


def _tolerance(*values):
    magnitude = max((abs(float(value)) for value in values if value is not None), default=1.0)
    return LEDGER_TOLERANCE * max(1.0, magnitude)


def parse_holding_row(row):
    ticker = str(row.get("ticker") or row.get("Ticker") or "").strip().upper()
    direction = str(row.get("direction") or row.get("Direction") or "").strip().upper()
    if not ticker:
        raise AccountSetupError("Each holding needs a ticker.")
    if direction not in ("LONG", "SHORT"):
        raise AccountSetupError(f"{ticker}: direction must be LONG or SHORT.")
    quantity = _finite(f"{ticker} quantity", row.get("quantity") or row.get("Quantity"))
    raw_price = row.get("average_price")
    if raw_price is None:
        raw_price = row.get("Average price")
    raw_cost = row.get("cost_basis")
    if raw_cost is None:
        raw_cost = row.get("Exact cost basis")
    price_blank = raw_price in (None, "")
    cost_blank = raw_cost in (None, "")
    if price_blank and cost_blank:
        raise AccountSetupError(
            f"{ticker}: enter an average purchase price or an exact cost basis."
        )
    if not price_blank and not cost_blank:
        average_price = _finite(f"{ticker} average purchase price", raw_price)
        cost_basis = _finite(f"{ticker} exact cost basis", raw_cost)
        implied = quantity * average_price
        if abs(implied - cost_basis) > _tolerance(implied, cost_basis):
            raise AccountSetupError(
                f"{ticker}: quantity × average price (${implied:,.4f}) does not "
                f"match exact cost basis (${cost_basis:,.4f})."
            )
    elif price_blank:
        cost_basis = _finite(f"{ticker} exact cost basis", raw_cost)
        average_price = cost_basis / quantity
    else:
        average_price = _finite(f"{ticker} average purchase price", raw_price)
        cost_basis = quantity * average_price
    return {
        "ticker": ticker,
        "direction": direction,
        "quantity": quantity,
        "entry_price": average_price,
        "allocated_capital": cost_basis,
        "strategy": SNAPSHOT_STRATEGY,
        "reason": SNAPSHOT_REASON,
    }


def parse_holdings_text(text):
    """Parse lines: ticker, direction, quantity, average_price[, cost_basis]."""
    rows = []
    for index, raw in enumerate((text or "").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 3:
            raise AccountSetupError(
                f"Line {index}: use ticker, LONG or SHORT, quantity, average price "
                "or leave average price blank and add exact cost basis."
            )
        while len(parts) < 5:
            parts.append("")
        ticker, direction, quantity, average_price, cost_basis = parts[:5]
        rows.append(
            {
                "ticker": ticker,
                "direction": direction,
                "quantity": quantity,
                "average_price": average_price,
                "cost_basis": cost_basis,
            }
        )
    return parse_holdings(rows)


def parse_holdings(rows):
    holdings = []
    seen = set()
    for row in rows or []:
        if not any(str(value).strip() for value in row.values() if value is not None):
            continue
        holding = parse_holding_row(row)
        key = (holding["ticker"], holding["direction"])
        if key in seen:
            raise AccountSetupError(
                f"Duplicate holding {holding['ticker']} {holding['direction']}. "
                "Combine lots into one quantity and average price or cost basis."
            )
        seen.add(key)
        holdings.append(holding)
    return holdings


def _normalized_opens(open_positions):
    rows = []
    for position in open_positions or []:
        rows.append(
            (
                str(position.get("ticker") or "").upper(),
                str(position.get("direction") or "").upper(),
                round(float(position.get("quantity") or 0.0), 8),
                round(float(position.get("entry_price") or 0.0), 8),
            )
        )
    return sorted(rows)


def _as_parsed_holdings(holdings):
    if not holdings:
        return []
    first = holdings[0]
    if first.get("entry_price") is not None and first.get("allocated_capital") is not None:
        return [
            parse_holding_row(
                {
                    "ticker": item["ticker"],
                    "direction": item["direction"],
                    "quantity": item["quantity"],
                    "average_price": item.get("entry_price"),
                    "cost_basis": item.get("allocated_capital"),
                }
            )
            for item in holdings
        ]
    return parse_holdings(holdings)


def preview_account_snapshot(
    starting_capital,
    cash,
    holdings,
    snapshot_as_of,
    db_path=None,
):
    starting_capital = _finite("Starting capital", starting_capital)
    cash = _finite("Current cash", cash, allow_zero=True)
    if snapshot_as_of is None or not str(snapshot_as_of).strip():
        raise AccountSetupError("Account snapshot date is required.")
    snapshot_as_of = str(snapshot_as_of).strip()
    parsed = _as_parsed_holdings(holdings)
    account = storage.get_account_state(db_path)
    open_positions = storage.get_open_positions(db_path)
    closed_trades = storage.get_closed_trades(db_path)
    realized = float(account["Realized P&L"] or 0.0)
    allocated = sum(item["allocated_capital"] for item in parsed)
    left = cash + allocated
    right = starting_capital + realized
    identity_ok = abs(left - right) <= _tolerance(left, right)
    already_matches = (
        abs(float(account["Cash"]) - cash) <= _tolerance(account["Cash"], cash)
        and abs(float(account["Starting Capital"] or 0.0) - starting_capital)
        <= _tolerance(account["Starting Capital"], starting_capital)
        and (account.get("Snapshot As Of") or "") == snapshot_as_of
        and _normalized_opens(open_positions) == _normalized_opens(parsed)
    )
    missing = []
    if not identity_ok:
        missing.append(
            "Cash plus the cost basis of holdings must equal starting capital "
            f"plus the realized P&L already stored from closed trades "
            f"(${realized:,.2f}). "
            f"Entered cash + holdings = ${left:,.2f}; "
            f"starting + realized P&L = ${right:,.2f}. "
            "Adjust starting capital, cash, or holdings. "
            "This form will not invent historical P&L or a balancing trade."
        )
    return {
        "can_save": identity_ok,
        "already_matches": already_matches,
        "starting_capital": starting_capital,
        "cash": cash,
        "snapshot_as_of": snapshot_as_of,
        "holdings": parsed,
        "allocated_capital": allocated,
        "preserved_realized_pnl": realized,
        "preserved_closed_trades": len(closed_trades),
        "identity_left": left,
        "identity_right": right,
        "missing_information": missing,
        "replaced_open_lots": len(open_positions),
        "notes": [
            "Imported holdings are an account snapshot, not recorded paper trades.",
            "Existing closed-trade history is kept and is not rewritten.",
            "Realized P&L is not invented.",
            "A backup is written before the live account is replaced.",
            "Any plan built on the previous portfolio will need regeneration.",
        ],
    }


def apply_account_snapshot(preview, db_path=None):
    if not preview or not preview.get("can_save"):
        raise AccountSetupError(
            " ".join(preview.get("missing_information") or ["The snapshot cannot be saved yet."])
        )
    if preview.get("already_matches"):
        return {
            "applied": False,
            "already_matches": True,
            "backup": None,
            "message": "This snapshot already matches the saved account. No lots were added.",
        }
    result = storage.replace_account_snapshot(
        preview["starting_capital"],
        preview["cash"],
        preview["holdings"],
        preview["snapshot_as_of"],
        db_path=db_path,
        create_backup=True,
    )
    return {
        "applied": True,
        "already_matches": False,
        "backup": result["backup"],
        "message": (
            f"Saved competition snapshot as of {preview['snapshot_as_of']}. "
            f"Backup: {result['backup']}."
        ),
    }
