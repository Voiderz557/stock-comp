"""SQLite-backed persistence for the paper-trading / competition dashboard.

Unlike Streamlit's `st.session_state` (which resets whenever the app process
restarts), this survives closing and reopening the app because it is backed
by a real file on disk. This module has no Streamlit dependency, so it can
be constructed and tested directly without running the dashboard.

The store is intentionally simple:

- One row of running account state (cash, realized P&L).
- One table of OPEN positions (LONG or SHORT).
- One table of CLOSED trades (history), written whenever a position closes.

SAFETY / ACCOUNTING NOTES (see also app/competition_dashboard.py):
- This module never sends orders anywhere. It only records what the user
  chose to open/close in the dashboard.
- No margin or leverage is modeled.
- Short-sale proceeds are never assumed spendable: opening a SHORT position
  does not change `cash`. `allocated_capital` for a SHORT is tracked purely
  as notional exposure for risk-limit purposes. Only the realized P&L is
  settled to cash when a SHORT is closed (a simplified cash-settlement
  model, not a real margin account).
- LONG and SHORT exposure share ONE capital limit ("starting capital"), not
  two separate wallets. Gross Exposure (Current Long Exposure + Current
  Short Exposure) may never exceed `starting_capital`. Opening a SHORT
  position permanently uses up part of that SAME shared capacity that a
  LONG would otherwise have been able to use, even though it does not touch
  `cash` directly. See `open_position()` and `valuation_summary()`.
"""

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


LONG = "LONG"
SHORT = "SHORT"
VALID_DIRECTIONS = (LONG, SHORT)


def _resolve_path(path):
    path = Path(path)
    if not path.is_absolute():
        path = Path(__file__).resolve().parents[1] / path
    return path.resolve()


def _utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class OpenPosition:
    id: int
    ticker: str
    direction: str
    entry_price: float
    quantity: float
    allocated_capital: float
    entry_timestamp: str
    strategy: str
    reason: str


@dataclass(frozen=True)
class ClosedTrade:
    id: int
    ticker: str
    direction: str
    entry_price: float
    exit_price: float
    quantity: float
    allocated_capital: float
    entry_timestamp: str
    exit_timestamp: str
    realized_pnl: float
    strategy: str
    reason: str


class PaperPortfolioStore:
    """Persistent paper-trading account backed by a SQLite file.

    Construct this with the same `db_path` every time (the dashboard uses
    `config.PAPER_PORTFOLIO_DB_PATH`) and every open position, closed trade,
    and cash balance will still be there after the process restarts.
    """

    def __init__(self, db_path, starting_cash=100_000.0):
        self.db_path = _resolve_path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._starting_cash = float(starting_cash)
        self._connection = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._initialize_schema()

    def close(self):
        self._connection.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def _initialize_schema(self):
        with self._connection:
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS account_state (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    cash REAL NOT NULL,
                    realized_pnl REAL NOT NULL DEFAULT 0.0,
                    starting_capital REAL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            # Migration for databases created before `starting_capital`
            # existed: add the column and best-effort backfill it so old
            # local databases keep working instead of crashing.
            existing_columns = {
                row["name"]
                for row in self._connection.execute(
                    "PRAGMA table_info(account_state)"
                ).fetchall()
            }
            if "starting_capital" not in existing_columns:
                self._connection.execute(
                    "ALTER TABLE account_state ADD COLUMN starting_capital REAL"
                )
            self._connection.execute(
                "UPDATE account_state SET starting_capital = cash "
                "WHERE starting_capital IS NULL"
            )
            self._connection.execute(
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
            self._connection.execute(
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
            # Only seeds a starting-cash row the FIRST time this database is
            # ever created. Every later run reuses whatever is already there.
            # `starting_capital` is set ONCE, here, and is never modified
            # again - it is the fixed, permanent shared exposure ceiling for
            # LONG + SHORT combined (see module docstring).
            self._connection.execute(
                """
                INSERT INTO account_state (id, cash, realized_pnl, starting_capital, updated_at)
                SELECT 1, ?, 0.0, ?, ?
                WHERE NOT EXISTS (SELECT 1 FROM account_state WHERE id = 1)
                """,
                (self._starting_cash, self._starting_cash, _utc_now_iso()),
            )

    # ------------------------------------------------------------------
    # Account state
    # ------------------------------------------------------------------
    def get_cash(self):
        row = self._connection.execute(
            "SELECT cash FROM account_state WHERE id = 1"
        ).fetchone()
        return float(row["cash"])

    def get_realized_pnl(self):
        row = self._connection.execute(
            "SELECT realized_pnl FROM account_state WHERE id = 1"
        ).fetchone()
        return float(row["realized_pnl"])

    def get_starting_capital(self):
        """The fixed, permanent shared LONG+SHORT exposure ceiling.

        Set once when this database is first created and never changed
        again (unaffected by realized P&L, cash spent, or any later
        `PaperPortfolioStore(..., starting_cash=...)` argument).
        """
        row = self._connection.execute(
            "SELECT starting_capital FROM account_state WHERE id = 1"
        ).fetchone()
        return float(row["starting_capital"])

    def _gross_exposure_components(self, current_prices, exclude_position_id=None):
        """(current_long_exposure, current_short_exposure) across ALL open
        positions, valued at CURRENT price.

        If a ticker's current price is not in `current_prices`, its own
        entry price is used as a conservative stand-in so a caller that
        forgets to pass live prices can never silently inflate available
        capacity. This is intentionally used for the shared capacity check
        in `open_position()`.
        """
        long_exposure = 0.0
        short_exposure = 0.0
        for position in self.list_open_positions():
            if position.id == exclude_position_id:
                continue
            price = current_prices.get(position.ticker, position.entry_price)
            if position.direction == LONG:
                long_exposure += position.quantity * price
            else:
                short_exposure += abs(position.quantity * price)
        return long_exposure, short_exposure

    def get_remaining_capacity(self, current_prices=None):
        """Shared LONG+SHORT capacity still available to open a NEW position.

        Gross Exposure = Current Long Exposure + Current Short Exposure
        Remaining Capacity = max(0, Starting Capital - Gross Exposure)

        LONG and SHORT draw from this SAME pool - opening $40,000 of shorts
        leaves only $60,000 of capacity for longs (and vice versa), out of
        a $100,000 starting capital account. This is never clamped negative
        for display purposes, but a hard-clamp at zero: existing positions
        appreciating past the cap does not go "negative capacity", it just
        means zero room for anything new.
        """
        current_prices = current_prices or {}
        long_exposure, short_exposure = self._gross_exposure_components(current_prices)
        gross_exposure = long_exposure + short_exposure
        return max(0.0, self.get_starting_capital() - gross_exposure)

    def _adjust_cash(self, delta):
        with self._connection:
            self._connection.execute(
                "UPDATE account_state SET cash = cash + ?, updated_at = ? WHERE id = 1",
                (delta, _utc_now_iso()),
            )

    def _add_realized_pnl(self, amount):
        with self._connection:
            self._connection.execute(
                "UPDATE account_state SET realized_pnl = realized_pnl + ?, "
                "updated_at = ? WHERE id = 1",
                (amount, _utc_now_iso()),
            )

    # ------------------------------------------------------------------
    # Opening / closing positions
    # ------------------------------------------------------------------
    def open_position(
        self,
        ticker,
        direction,
        entry_price,
        allocated_capital,
        strategy,
        reason="",
        entry_timestamp=None,
        current_prices=None,
    ):
        """Open a new paper position and return its OpenPosition record.

        Cash accounting (unchanged from before):
        - LONG: `allocated_capital` is spent immediately, like a real buy,
          and is deducted from cash.
        - SHORT: no cash changes hands when opening. No margin is modeled
          and short-sale proceeds are never assumed spendable, so opening a
          SHORT does NOT increase available cash/buying power.

        Shared gross-exposure cap (applies to BOTH directions):
        LONG and SHORT are NOT two separate wallets. `allocated_capital`
        must fit within the SAME remaining capacity - see
        `get_remaining_capacity()`. `current_prices` (ticker -> price) is
        used to mark existing open positions to market before checking; any
        ticker missing from it falls back to that position's own entry
        price (conservative, never under-counts existing exposure).
        """
        if direction not in VALID_DIRECTIONS:
            raise ValueError(f"Unknown direction '{direction}'.")
        if entry_price <= 0:
            raise ValueError("Entry price must be positive.")
        if allocated_capital <= 0:
            raise ValueError("Allocated capital must be positive.")

        remaining_capacity = self.get_remaining_capacity(current_prices)
        if allocated_capital > remaining_capacity + 1e-6:
            raise ValueError(
                f"Allocated capital (${allocated_capital:,.2f}) exceeds the "
                f"remaining shared gross-exposure capacity "
                f"(${remaining_capacity:,.2f}). LONG and SHORT exposure "
                "share ONE capital limit, not separate wallets."
            )
        if direction == LONG and allocated_capital > self.get_cash() + 1e-6:
            raise ValueError("Allocated capital exceeds available cash.")

        quantity = allocated_capital / entry_price
        entry_timestamp = entry_timestamp or _utc_now_iso()

        with self._connection:
            cursor = self._connection.execute(
                """
                INSERT INTO open_positions (
                    ticker, direction, entry_price, quantity,
                    allocated_capital, entry_timestamp, strategy, reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ticker,
                    direction,
                    float(entry_price),
                    quantity,
                    float(allocated_capital),
                    entry_timestamp,
                    strategy,
                    reason,
                ),
            )
            position_id = cursor.lastrowid

        if direction == LONG:
            self._adjust_cash(-allocated_capital)

        return self.get_open_position(position_id)

    def close_position(self, position_id, exit_price, exit_timestamp=None):
        """Close an open position, move it into closed-trade history, and
        return its realized P&L.

        Realized P&L settlement:
        - LONG: sale proceeds (quantity * exit_price) are credited to cash.
        - SHORT: no notional exposure ever touched cash while open, so on
          close only the realized P&L (positive or negative) is settled to
          cash. This is a simplified cash-settlement model, not a real
          margin account.
        """
        if exit_price <= 0:
            raise ValueError("Exit price must be positive.")

        position = self.get_open_position(position_id)
        if position is None:
            raise ValueError(f"No open position with id {position_id}.")

        exit_timestamp = exit_timestamp or _utc_now_iso()

        if position.direction == LONG:
            realized_pnl = (exit_price - position.entry_price) * position.quantity
            cash_delta = position.quantity * exit_price
        else:
            realized_pnl = (position.entry_price - exit_price) * position.quantity
            cash_delta = realized_pnl

        with self._connection:
            self._connection.execute(
                """
                INSERT INTO closed_trades (
                    ticker, direction, entry_price, exit_price, quantity,
                    allocated_capital, entry_timestamp, exit_timestamp,
                    realized_pnl, strategy, reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    position.ticker,
                    position.direction,
                    position.entry_price,
                    float(exit_price),
                    position.quantity,
                    position.allocated_capital,
                    position.entry_timestamp,
                    exit_timestamp,
                    realized_pnl,
                    position.strategy,
                    position.reason,
                ),
            )
            self._connection.execute(
                "DELETE FROM open_positions WHERE id = ?", (position_id,)
            )

        self._adjust_cash(cash_delta)
        self._add_realized_pnl(realized_pnl)

        return realized_pnl

    # ------------------------------------------------------------------
    # Reading state
    # ------------------------------------------------------------------
    def get_open_position(self, position_id):
        row = self._connection.execute(
            "SELECT * FROM open_positions WHERE id = ?", (position_id,)
        ).fetchone()
        return self._row_to_open_position(row) if row else None

    def list_open_positions(self):
        rows = self._connection.execute(
            "SELECT * FROM open_positions ORDER BY entry_timestamp ASC"
        ).fetchall()
        return [self._row_to_open_position(row) for row in rows]

    def list_closed_trades(self):
        rows = self._connection.execute(
            "SELECT * FROM closed_trades ORDER BY exit_timestamp ASC"
        ).fetchall()
        return [self._row_to_closed_trade(row) for row in rows]

    @staticmethod
    def _row_to_open_position(row):
        return OpenPosition(
            id=row["id"],
            ticker=row["ticker"],
            direction=row["direction"],
            entry_price=row["entry_price"],
            quantity=row["quantity"],
            allocated_capital=row["allocated_capital"],
            entry_timestamp=row["entry_timestamp"],
            strategy=row["strategy"],
            reason=row["reason"],
        )

    @staticmethod
    def _row_to_closed_trade(row):
        return ClosedTrade(
            id=row["id"],
            ticker=row["ticker"],
            direction=row["direction"],
            entry_price=row["entry_price"],
            exit_price=row["exit_price"],
            quantity=row["quantity"],
            allocated_capital=row["allocated_capital"],
            entry_timestamp=row["entry_timestamp"],
            exit_timestamp=row["exit_timestamp"],
            realized_pnl=row["realized_pnl"],
            strategy=row["strategy"],
            reason=row["reason"],
        )

    # ------------------------------------------------------------------
    # Valuation using current prices (read-only: never mutates stored data)
    # ------------------------------------------------------------------
    def valuation_summary(self, current_prices):
        """Recalculate unrealized P&L and total portfolio value.

        `current_prices` maps ticker -> latest known price. A missing price
        leaves that position's P&L/value as None (still listed, just
        excluded from the totals) instead of crashing the dashboard.

        This method is purely a read-time recalculation: it NEVER overwrites
        the stored entry price, quantity, or timestamps of any position.

        SHORT exposure is always computed from the CURRENT price, not the
        entry price - a short's notional risk moves with the market even
        though the entry price used for P&L never changes:

            Initial Short Exposure = abs(quantity * entry_price)
            Current Short Exposure = abs(quantity * current_price)

        `Current Short Exposure` (not the initial one) is what feeds
        `Gross Exposure` / `Net Exposure` below, and rises/falls exactly as
        the price rises/falls - it is deliberately NOT the same as
        unrealized P&L, which moves in the opposite direction for shorts.

        Equity model (no margin):
            Total Portfolio Value =
                Cash
                + sum(LONG market values, KNOWN prices only)
                + sum(SHORT unrealized P&L, KNOWN prices only)

        Shared gross-exposure capacity (see also `open_position()` and
        `get_remaining_capacity()` - LONG and SHORT share ONE limit, not two
        separate wallets):
            Current Long Exposure  = sum(quantity * price) for LONGs
            Current Short Exposure = sum(abs(quantity * price)) for SHORTs
            Gross Exposure = Current Long Exposure + Current Short Exposure
            Net Exposure   = Current Long Exposure - Current Short Exposure
            Remaining Capacity = max(0, Starting Capital - Gross Exposure)

        Unlike `Total Portfolio Value` above, these exposure figures fall
        back to a position's own entry price when its current price is
        missing from `current_prices` (conservative: never silently shrinks
        tracked exposure just because a live price wasn't supplied), so they
        always match exactly what `open_position()` will enforce.

        A LONG position's cash was already spent when it opened, so its
        current market value replaces that cash in the equity total. A
        SHORT position never touched cash when it opened, so it only
        contributes its unrealized P&L (gain if the price fell, loss if it
        rose) to equity - exposure is tracked separately from equity for
        risk-limit purposes.
        """
        cash = self.get_cash()
        positions = []
        known_long_market_value = 0.0
        total_unrealized_pnl = 0.0

        for position in self.list_open_positions():
            current_price = current_prices.get(position.ticker)
            unrealized_pnl = None
            market_value = None
            initial_short_exposure = None
            position_current_short_exposure = None

            if position.direction == SHORT:
                initial_short_exposure = abs(position.quantity * position.entry_price)

            if current_price is not None:
                if position.direction == LONG:
                    market_value = position.quantity * current_price
                    unrealized_pnl = (
                        current_price - position.entry_price
                    ) * position.quantity
                    known_long_market_value += market_value
                else:
                    unrealized_pnl = (
                        position.entry_price - current_price
                    ) * position.quantity
                    position_current_short_exposure = abs(
                        position.quantity * current_price
                    )
                total_unrealized_pnl += unrealized_pnl

            positions.append(
                {
                    "Position": position,
                    "Current Price": current_price,
                    "Market Value": market_value,
                    "Unrealized P&L": unrealized_pnl,
                    "Initial Short Exposure": initial_short_exposure,
                    "Current Short Exposure": position_current_short_exposure,
                }
            )

        short_unrealized_total = sum(
            item["Unrealized P&L"]
            for item in positions
            if item["Position"].direction == SHORT
            and item["Unrealized P&L"] is not None
        )
        total_value = cash + known_long_market_value + short_unrealized_total

        current_long_exposure, current_short_exposure = self._gross_exposure_components(
            current_prices
        )
        gross_exposure = current_long_exposure + current_short_exposure
        net_exposure = current_long_exposure - current_short_exposure
        starting_capital = self.get_starting_capital()
        remaining_capacity = max(0.0, starting_capital - gross_exposure)

        return {
            "Cash": cash,
            "Realized P&L": self.get_realized_pnl(),
            "Positions": positions,
            "Starting Capital": starting_capital,
            "Current Long Exposure": current_long_exposure,
            "Current Short Exposure": current_short_exposure,
            # Alias for "Current Short Exposure" - the aggregate short book,
            # valued at CURRENT market price (never entry price).
            "Gross Short Exposure": current_short_exposure,
            "Gross Exposure": gross_exposure,
            "Net Exposure": net_exposure,
            "Remaining Capacity": remaining_capacity,
            "Total Unrealized P&L": total_unrealized_pnl,
            "Total Portfolio Value": total_value,
        }
