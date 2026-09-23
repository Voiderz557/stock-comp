"""SQLite-backed storage for the paper-trading dashboard.

This module owns all persistence for simulated ("paper") trades: it never
places a real order and never talks to a broker. It stores exactly three
things in a local SQLite database:

    account_state   - a single row tracking cash, realized P&L, and the
                       starting capital the paper account was seeded with.
    open_positions  - one row per currently-open simulated position.
    closed_trades   - one row per simulated position that has been closed,
                       kept as permanent history.

P&L and mark-to-market exposure math live in `paper_trading.portfolio`.
This module reads and writes rows, then enforces two independent limits on
every new open:

    1. available cash (LONG and SHORT both debit `allocated_capital`)
    2. remaining shared capacity
       (starting_capital - current long market value
        - abs(current short market value), floored at 0)
"""

import math
import os
import shutil
import sqlite3
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from config import MAX_POSITION_VALUE, MIN_STOCK_PRICE
from paper_trading.ledger import assert_accounting_consistent, reconcile_ledger
from paper_trading.portfolio import (
    compute_exposure_summary,
    position_notional_exposure,
    same_ticker_market_value,
)


PAPER_DATA_DIR = "paper_trading_data"
DEFAULT_DB_PATH = "paper_trading_data/paper_portfolio.sqlite3"
STARTING_CAPITAL = 100_000.0
_CONNECT_TIMEOUT_SECONDS = 30.0
_ALLOCATION_MATCH_TOLERANCE = 1e-6

_ACCOUNT_STATE_COLUMNS = ("cash", "realized_pnl", "updated_at", "starting_capital")
_OPEN_POSITION_COLUMNS = (
    "id",
    "ticker",
    "direction",
    "entry_price",
    "quantity",
    "allocated_capital",
    "entry_timestamp",
    "strategy",
    "reason",
)
_CLOSED_TRADE_COLUMNS = (
    "id",
    "ticker",
    "direction",
    "entry_price",
    "exit_price",
    "quantity",
    "allocated_capital",
    "entry_timestamp",
    "exit_timestamp",
    "realized_pnl",
    "strategy",
    "reason",
)


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _resolve_project_path(path):
    path = Path(path)
    if not path.is_absolute():
        path = Path(__file__).resolve().parents[1] / path
    return path.resolve()


def _resolve_db_path(db_path=None):
    if db_path is not None:
        return _resolve_project_path(db_path)
    env_db = os.environ.get("STOCK_COMP_PAPER_DB")
    if env_db:
        return Path(env_db).resolve()
    env_dir = os.environ.get("STOCK_COMP_PAPER_DATA_DIR")
    if env_dir:
        return (Path(env_dir) / "paper_portfolio.sqlite3").resolve()
    if getattr(sys, "frozen", False):
        local_app = os.environ.get("LOCALAPPDATA")
        root = (
            Path(local_app) / "StockComp"
            if local_app
            else Path.home() / "AppData" / "Local" / "StockComp"
        )
        return (root / PAPER_DATA_DIR / "paper_portfolio.sqlite3").resolve()
    return _resolve_project_path(DEFAULT_DB_PATH)


def paper_data_directory(db_path=None):
    """Local directory that holds the paper ledger and saved trading plans."""
    return _resolve_db_path(db_path).parent


def _connect(db_path=None):
    resolved = _resolve_db_path(db_path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(resolved, timeout=_CONNECT_TIMEOUT_SECONDS)
    connection.row_factory = sqlite3.Row
    return connection


@contextmanager
def _immediate_transaction(db_path=None):
    """One SQLite connection with BEGIN IMMEDIATE so concurrent opens serialize.

    Account read, exposure check, position write, and cash update all happen
    on this connection before COMMIT.
    """
    connection = _connect(db_path)
    connection.isolation_level = None
    started = False
    try:
        connection.execute("BEGIN IMMEDIATE")
        started = True
        yield connection
        connection.execute("COMMIT")
    except Exception:
        if started:
            connection.execute("ROLLBACK")
        raise
    finally:
        connection.close()


def _require_finite_positive(name, value):
    if value is None:
        raise ValueError(f"{name} must be a finite positive number.")
    try:
        value = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a finite positive number.") from error
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a finite positive number.")
    return value


def _table_columns(connection, table_name):
    return {
        row["name"]
        for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    }


def initialize_storage(db_path=None, starting_capital=STARTING_CAPITAL):
    """Create the schema if needed and seed the account with starting cash.

    Safe to call every time the dashboard loads: it is a no-op once the
    database already has a seeded `account_state` row. Existing
    `starting_capital` is never overwritten, and a missing value is never
    inferred from current cash.
    """
    connection = _connect(db_path)
    try:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS account_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                cash REAL NOT NULL,
                realized_pnl REAL NOT NULL DEFAULT 0.0,
                updated_at TEXT NOT NULL,
                starting_capital REAL
            )
            """
        )
        account_columns = _table_columns(connection, "account_state")
        if "starting_capital" not in account_columns:
            connection.execute(
                "ALTER TABLE account_state ADD COLUMN starting_capital REAL"
            )
        if "snapshot_as_of" not in account_columns:
            connection.execute("ALTER TABLE account_state ADD COLUMN snapshot_as_of TEXT")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS open_positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL,
                direction TEXT NOT NULL CHECK (direction IN ('LONG', 'SHORT')),
                entry_price REAL NOT NULL,
                quantity REAL NOT NULL,
                allocated_capital REAL NOT NULL,
                entry_timestamp TEXT NOT NULL,
                strategy TEXT NOT NULL,
                reason TEXT NOT NULL DEFAULT ''
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS closed_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL,
                direction TEXT NOT NULL CHECK (direction IN ('LONG', 'SHORT')),
                entry_price REAL NOT NULL,
                exit_price REAL NOT NULL,
                quantity REAL NOT NULL,
                allocated_capital REAL NOT NULL,
                entry_timestamp TEXT NOT NULL,
                exit_timestamp TEXT NOT NULL,
                realized_pnl REAL NOT NULL,
                strategy TEXT NOT NULL,
                reason TEXT NOT NULL DEFAULT ''
            )
            """
        )
        from paper_trading.plan_store import ensure_plan_schema

        ensure_plan_schema(connection)
        existing = connection.execute(
            "SELECT id FROM account_state WHERE id = 1"
        ).fetchone()
        if existing is None:
            starting_capital = _require_finite_positive("starting_capital", starting_capital)
            connection.execute(
                "INSERT INTO account_state "
                "(id, cash, realized_pnl, updated_at, starting_capital) "
                "VALUES (1, ?, 0.0, ?, ?)",
                (starting_capital, _now_iso(), starting_capital),
            )
        connection.commit()
    finally:
        connection.close()


def get_account_state(db_path=None):
    """Return {Cash, Realized P&L, Starting Capital, Updated At, Snapshot As Of}."""
    connection = _connect(db_path)
    try:
        row = connection.execute(
            "SELECT cash, realized_pnl, updated_at, starting_capital, snapshot_as_of "
            "FROM account_state WHERE id = 1"
        ).fetchone()
    except sqlite3.OperationalError:
        try:
            row = connection.execute(
                "SELECT cash, realized_pnl, updated_at, starting_capital "
                "FROM account_state WHERE id = 1"
            ).fetchone()
        except sqlite3.OperationalError:
            row = None
    finally:
        connection.close()
    if row is None:
        raise RuntimeError(
            "Paper trading storage has not been initialized. "
            "Call initialize_storage() first."
        )
    as_of = row["snapshot_as_of"] if "snapshot_as_of" in row.keys() else None
    return {
        "Cash": row["cash"],
        "Realized P&L": row["realized_pnl"],
        "Updated At": row["updated_at"],
        "Starting Capital": row["starting_capital"],
        "Snapshot As Of": as_of,
    }


def get_open_positions(db_path=None):
    """Return every open position as a list of dicts, oldest entry first."""
    connection = _connect(db_path)
    try:
        rows = connection.execute(
            "SELECT * FROM open_positions ORDER BY entry_timestamp ASC, id ASC"
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []
    finally:
        connection.close()
    return [dict(row) for row in rows]


def get_closed_trades(db_path=None):
    """Return every closed trade as a list of dicts, most recent exit first."""
    connection = _connect(db_path)
    try:
        rows = connection.execute(
            "SELECT * FROM closed_trades ORDER BY exit_timestamp DESC, id DESC"
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []
    finally:
        connection.close()
    return [dict(row) for row in rows]


def audit_ledger(current_prices=None, db_path=None):
    """Read-only reconciliation of the persisted paper ledger."""
    return reconcile_ledger(
        get_account_state(db_path),
        get_open_positions(db_path),
        get_closed_trades(db_path),
        current_prices=current_prices,
    )


def _load_open_positions(connection):
    rows = connection.execute(
        "SELECT * FROM open_positions ORDER BY entry_timestamp ASC, id ASC"
    ).fetchall()
    return [dict(row) for row in rows]


def _load_closed_trades(connection):
    rows = connection.execute(
        "SELECT * FROM closed_trades ORDER BY exit_timestamp DESC, id DESC"
    ).fetchall()
    return [dict(row) for row in rows]


def _account_from_row(row):
    return {
        "Cash": row["cash"],
        "Realized P&L": row["realized_pnl"],
        "Updated At": row["updated_at"],
        "Starting Capital": row["starting_capital"],
    }


def _ledger_snapshot(connection):
    try:
        account_row = connection.execute(
            "SELECT cash, realized_pnl, updated_at, starting_capital "
            "FROM account_state WHERE id = 1"
        ).fetchone()
    except sqlite3.OperationalError:
        account_row = None
    if account_row is None:
        raise RuntimeError(
            "Paper trading storage has not been initialized. "
            "Call initialize_storage() first."
        )
    return (
        _account_from_row(account_row),
        _load_open_positions(connection),
        _load_closed_trades(connection),
    )


def _assert_ledger_after_mutation(connection):
    account, opens, closed = _ledger_snapshot(connection)
    assert_accounting_consistent(account, opens, closed)


def open_position(
    ticker,
    entry_price,
    quantity=None,
    allocated_capital=None,
    strategy="Manual",
    reason="",
    direction="LONG",
    entry_timestamp=None,
    current_prices=None,
    min_stock_price=MIN_STOCK_PRICE,
    max_position_value=MAX_POSITION_VALUE,
    db_path=None,
):
    """Open a simulated position, debiting cash and consuming shared capacity.

    Provide either `quantity` or `allocated_capital` (or both, if they agree
    with `entry_price`). Both LONG and SHORT debit `allocated_capital` from
    cash. A new open is also rejected unless it fits in remaining shared
    capacity, marked to market from `current_prices` (no entry-price fallback).

    Competition purchase constraints (config.MIN_STOCK_PRICE /
    MAX_POSITION_VALUE) are enforced here, not only in the UI. Repeated
    LONG purchases of the same ticker are aggregated at current market
    value plus the proposed purchase. Appreciation above the cap never
    sells automatically.
    """
    if direction not in ("LONG", "SHORT"):
        raise ValueError("direction must be 'LONG' or 'SHORT'.")
    if not ticker:
        raise ValueError("ticker is required.")
    entry_price = _require_finite_positive("entry_price", entry_price)
    if quantity is None and allocated_capital is None:
        raise ValueError("Provide either quantity or allocated_capital.")

    parsed_quantity = None if quantity is None else _require_finite_positive("quantity", quantity)
    parsed_allocation = (
        None
        if allocated_capital is None
        else _require_finite_positive("allocated_capital", allocated_capital)
    )

    if parsed_quantity is None:
        quantity = parsed_allocation / entry_price
        allocated_capital = parsed_allocation
    elif parsed_allocation is None:
        quantity = parsed_quantity
        allocated_capital = quantity * entry_price
    else:
        implied_allocation = parsed_quantity * entry_price
        tolerance = _ALLOCATION_MATCH_TOLERANCE * max(
            1.0, abs(implied_allocation), abs(parsed_allocation)
        )
        if abs(implied_allocation - parsed_allocation) > tolerance:
            raise ValueError(
                "quantity and allocated_capital are inconsistent with entry_price "
                f"(quantity * entry_price = ${implied_allocation:,.4f}, "
                f"allocated_capital = ${parsed_allocation:,.4f})."
            )
        quantity = parsed_quantity
        allocated_capital = parsed_allocation

    if min_stock_price is not None and entry_price < float(min_stock_price) - 1e-9:
        raise ValueError(
            f"Entry price ${entry_price:,.2f} is below the minimum stock price "
            f"${float(min_stock_price):,.2f}."
        )

    new_exposure = position_notional_exposure(quantity, entry_price)
    entry_timestamp = entry_timestamp or _now_iso()

    with _immediate_transaction(db_path) as connection:
        try:
            account_row = connection.execute(
                "SELECT cash, realized_pnl, updated_at, starting_capital "
                "FROM account_state WHERE id = 1"
            ).fetchone()
        except sqlite3.OperationalError:
            account_row = None
        if account_row is None:
            raise RuntimeError(
                "Paper trading storage has not been initialized. "
                "Call initialize_storage() first."
            )

        cash = float(account_row["cash"])
        if allocated_capital > cash + 1e-6:
            raise ValueError(
                f"Insufficient paper cash: requested ${allocated_capital:,.2f} "
                f"but only ${cash:,.2f} is available."
            )

        open_positions = _load_open_positions(connection)
        if direction == "LONG" and max_position_value is not None:
            existing_value = same_ticker_market_value(
                open_positions, ticker, "LONG", current_prices
            )
            proposed_total = existing_value + new_exposure
            cap = float(max_position_value)
            if proposed_total > cap + 1e-6:
                raise ValueError(
                    f"Per-stock purchase cap ${cap:,.2f} exceeded for {ticker}: "
                    f"current market value of existing lots ${existing_value:,.2f} "
                    f"plus this purchase ${new_exposure:,.2f} = ${proposed_total:,.2f}. "
                    "Appreciation above the cap is allowed to remain open; "
                    "additional purchases that would grow the position further "
                    "are rejected. Nothing is sold automatically."
                )

        exposure = compute_exposure_summary(
            open_positions, current_prices, account_row["starting_capital"]
        )
        remaining_capacity = exposure["Remaining Capacity"]
        if new_exposure > remaining_capacity + 1e-6:
            raise ValueError(
                f"Allocated exposure (${new_exposure:,.2f}) exceeds remaining "
                f"shared capacity (${remaining_capacity:,.2f}). Gross exposure "
                f"(${exposure['Gross Exposure']:,.2f}) plus this position would "
                f"exceed starting capital (${exposure['Starting Capital']:,.2f}). "
                "LONG and SHORT share one ceiling; market moves above it block "
                "new opens but do not force closes."
            )

        cursor = connection.execute(
            "INSERT INTO open_positions "
            "(ticker, direction, entry_price, quantity, allocated_capital, "
            "entry_timestamp, strategy, reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                ticker,
                direction,
                entry_price,
                quantity,
                allocated_capital,
                entry_timestamp,
                strategy,
                reason,
            ),
        )
        connection.execute(
            "UPDATE account_state SET cash = cash - ?, updated_at = ? WHERE id = 1",
            (allocated_capital, _now_iso()),
        )
        _assert_ledger_after_mutation(connection)
        _mark_latest_plan_needs_regeneration(
            connection,
            "Account snapshot changed after a recorded paper open.",
        )
        return cursor.lastrowid


def _mark_latest_plan_needs_regeneration(connection, reason):
    """Flag the newest saved plan after a ledger mutation in this transaction.

    No-op when no plan has been saved. Does not delete plans or positions.
    """
    try:
        row = connection.execute(
            "SELECT id, needs_regeneration FROM trading_plans ORDER BY id DESC LIMIT 1"
        ).fetchone()
    except sqlite3.OperationalError:
        return
    if row is None or row["needs_regeneration"]:
        return
    connection.execute(
        "UPDATE trading_plans SET needs_regeneration = 1, stale_reason = ? WHERE id = ?",
        (reason, row["id"]),
    )


def close_position(position_id, exit_price, exit_timestamp=None, db_path=None, return_id=False):
    """Close an open position at `exit_price`, crediting cash and realized P&L.

    Returns the realized P&L (positive = paper profit) for this trade.
    Pass `return_id=True` to also receive the new closed-trade row id.
    Closing is always allowed even if mark-to-market gross exposure is
    already above starting capital - this never forces liquidation, it only
    records an explicit close.
    """
    exit_price = _require_finite_positive("exit_price", exit_price)
    exit_timestamp = exit_timestamp or _now_iso()

    with _immediate_transaction(db_path) as connection:
        position = connection.execute(
            "SELECT * FROM open_positions WHERE id = ?", (position_id,)
        ).fetchone()
        if position is None:
            raise ValueError(f"No open position with id {position_id}.")
        position = dict(position)

        if position["direction"] == "LONG":
            realized_pnl = (exit_price - position["entry_price"]) * position["quantity"]
        else:
            realized_pnl = (position["entry_price"] - exit_price) * position["quantity"]
        proceeds = position["allocated_capital"] + realized_pnl

        closed_cursor = connection.execute(
            "INSERT INTO closed_trades "
            "(ticker, direction, entry_price, exit_price, quantity, "
            "allocated_capital, entry_timestamp, exit_timestamp, realized_pnl, "
            "strategy, reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                position["ticker"],
                position["direction"],
                position["entry_price"],
                exit_price,
                position["quantity"],
                position["allocated_capital"],
                position["entry_timestamp"],
                exit_timestamp,
                realized_pnl,
                position["strategy"],
                position["reason"],
            ),
        )
        connection.execute("DELETE FROM open_positions WHERE id = ?", (position_id,))
        connection.execute(
            "UPDATE account_state "
            "SET cash = cash + ?, realized_pnl = realized_pnl + ?, updated_at = ? "
            "WHERE id = 1",
            (proceeds, realized_pnl, _now_iso()),
        )
        _assert_ledger_after_mutation(connection)
        _mark_latest_plan_needs_regeneration(
            connection,
            "Account snapshot changed after a recorded paper close.",
        )
        closed_trade_id = closed_cursor.lastrowid
        if return_id:
            return realized_pnl, closed_trade_id
        return realized_pnl


SNAPSHOT_STRATEGY = "Account snapshot"
SNAPSHOT_REASON = (
    "Imported competition holding (account snapshot, not a recorded paper trade)."
)


def backup_ledger(db_path=None):
    """Copy the SQLite ledger next to the live file. Never deletes the source."""
    source = _resolve_db_path(db_path)
    if not source.is_file():
        raise FileNotFoundError(f"No paper ledger to back up at {source}.")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = source.parent / "backups" / f"paper_portfolio_{stamp}.sqlite3"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return destination


def replace_account_snapshot(
    starting_capital,
    cash,
    holdings,
    snapshot_as_of,
    db_path=None,
    create_backup=True,
):
    """Replace live cash/holdings with a competition snapshot.

    Closed-trade history and its realized P&L are kept. No balancing
    trades are invented. Callers must already satisfy cash identity.
    """
    starting_capital = _require_finite_positive("starting_capital", starting_capital)
    if cash is None:
        raise ValueError("cash is required.")
    try:
        cash = float(cash)
    except (TypeError, ValueError) as error:
        raise ValueError("cash must be a finite number.") from error
    if not math.isfinite(cash):
        raise ValueError("cash must be a finite number.")
    if snapshot_as_of is None or not str(snapshot_as_of).strip():
        raise ValueError("Account snapshot date is required.")
    snapshot_as_of = str(snapshot_as_of).strip()

    backup_path = backup_ledger(db_path) if create_backup else None
    with _immediate_transaction(db_path) as connection:
        account, _opens, closed = _ledger_snapshot(connection)
        realized = float(account["Realized P&L"] or 0.0)
        connection.execute(
            "UPDATE account_state "
            "SET cash = ?, starting_capital = ?, snapshot_as_of = ?, updated_at = ? "
            "WHERE id = 1",
            (cash, starting_capital, snapshot_as_of, _now_iso()),
        )
        connection.execute("DELETE FROM open_positions")
        for holding in holdings:
            connection.execute(
                "INSERT INTO open_positions "
                "(ticker, direction, entry_price, quantity, allocated_capital, "
                "entry_timestamp, strategy, reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    holding["ticker"],
                    holding["direction"],
                    holding["entry_price"],
                    holding["quantity"],
                    holding["allocated_capital"],
                    snapshot_as_of,
                    SNAPSHOT_STRATEGY,
                    SNAPSHOT_REASON,
                ),
            )
        _assert_ledger_after_mutation(connection)
        _mark_latest_plan_needs_regeneration(
            connection,
            "Account snapshot was replaced from a manual competition setup.",
        )
    return {
        "backup": None if backup_path is None else str(backup_path),
        "realized_pnl": realized,
        "closed_trade_count": len(closed),
    }
