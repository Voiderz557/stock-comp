"""SQLite-backed storage for the paper-trading dashboard.

This module owns all persistence for simulated ("paper") trades: it never
places a real order and never talks to a broker. It stores exactly three
things in a local SQLite database:

    account_state   - a single row tracking cash, realized P&L, and the
                       starting capital the paper account was seeded with.
    open_positions  - one row per currently-open simulated position.
    closed_trades   - one row per simulated position that has been closed,
                       kept as permanent history.

All money math (unrealized P&L, portfolio value, etc.) lives in
`paper_trading.portfolio`, which is pure and does not touch the database.
This module is intentionally "dumb": it reads and writes rows and enforces
just enough validation to keep the paper ledger internally consistent
(e.g. you cannot allocate more paper cash than you have).
"""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_DB_PATH = "paper_trading_data/paper_portfolio.sqlite3"
STARTING_CAPITAL = 100_000.0

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
    return _resolve_project_path(db_path or DEFAULT_DB_PATH)


def _connect(db_path=None):
    resolved = _resolve_db_path(db_path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(resolved)
    connection.row_factory = sqlite3.Row
    return connection


def initialize_storage(db_path=None, starting_capital=STARTING_CAPITAL):
    """Create the schema if needed and seed the account with starting cash.

    Safe to call every time the dashboard loads: it is a no-op once the
    database already has a seeded `account_state` row.
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
        existing = connection.execute(
            "SELECT id FROM account_state WHERE id = 1"
        ).fetchone()
        if existing is None:
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
    """Return {Cash, Realized P&L, Starting Capital, Updated At}."""
    connection = _connect(db_path)
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
    return {
        "Cash": row["cash"],
        "Realized P&L": row["realized_pnl"],
        "Updated At": row["updated_at"],
        "Starting Capital": row["starting_capital"],
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


def open_position(
    ticker,
    entry_price,
    quantity=None,
    allocated_capital=None,
    strategy="Manual",
    reason="",
    direction="LONG",
    entry_timestamp=None,
    db_path=None,
):
    """Open a simulated position and debit the paper cash balance.

    Provide either `quantity` or `allocated_capital` (not necessarily
    both) - the other is derived from `entry_price`. Raises `ValueError` if
    the requested allocation exceeds available paper cash.
    """
    if direction not in ("LONG", "SHORT"):
        raise ValueError("direction must be 'LONG' or 'SHORT'.")
    if entry_price is None or entry_price <= 0:
        raise ValueError("entry_price must be a positive number.")
    if quantity is None and allocated_capital is None:
        raise ValueError("Provide either quantity or allocated_capital.")

    entry_price = float(entry_price)
    if quantity is None:
        allocated_capital = float(allocated_capital)
        quantity = allocated_capital / entry_price
    elif allocated_capital is None:
        quantity = float(quantity)
        allocated_capital = quantity * entry_price
    else:
        quantity = float(quantity)
        allocated_capital = float(allocated_capital)

    if quantity <= 0 or allocated_capital <= 0:
        raise ValueError("quantity and allocated_capital must be positive.")

    entry_timestamp = entry_timestamp or _now_iso()

    connection = _connect(db_path)
    try:
        try:
            account_row = connection.execute(
                "SELECT cash FROM account_state WHERE id = 1"
            ).fetchone()
        except sqlite3.OperationalError:
            account_row = None
        if account_row is None:
            raise RuntimeError(
                "Paper trading storage has not been initialized. "
                "Call initialize_storage() first."
            )
        cash = account_row["cash"]
        if allocated_capital > cash + 1e-6:
            raise ValueError(
                f"Insufficient paper cash: requested ${allocated_capital:,.2f} "
                f"but only ${cash:,.2f} is available."
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
        connection.commit()
        return cursor.lastrowid
    finally:
        connection.close()


def close_position(position_id, exit_price, exit_timestamp=None, db_path=None):
    """Close an open position at `exit_price`, crediting cash and realized P&L.

    Returns the realized P&L (positive = paper profit) for this trade.
    """
    if exit_price is None or exit_price <= 0:
        raise ValueError("exit_price must be a positive number.")
    exit_price = float(exit_price)
    exit_timestamp = exit_timestamp or _now_iso()

    connection = _connect(db_path)
    try:
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

        connection.execute(
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
        connection.commit()
        return realized_pnl
    finally:
        connection.close()
