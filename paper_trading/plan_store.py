"""Saved daily trading plans in the paper-trading SQLite database.

Plans are recommendations. They become trades only when
`execute_recommendation` calls the existing `open_position` / `close_position`
functions. Generating a plan inserts a new row and leaves older plans in place.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from paper_trading.trading_plan import REVIEW_EXIT, UNAVAILABLE


STATUS_OPEN = "OPEN"
STATUS_EXECUTING = "EXECUTING"
STATUS_EXECUTED = "EXECUTED"
STATUS_DISMISSED = "DISMISSED"

SECTION_BUY = "BUY"
SECTION_HOLDING = "HOLDING"

DROPPED_CANDIDATE_NOTE = (
    "A dropped candidate is no longer a proposed buy. "
    "It is not an instruction to sell."
)


class DuplicateRecommendationError(RuntimeError):
    """The recommendation was already executed, dismissed, or is in progress."""


class StalePlanError(RuntimeError):
    """The plan's account snapshot no longer matches the ledger."""


def ensure_plan_schema(connection):
    """Create plan tables if missing. Never drops or rewrites existing rows."""
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS trading_plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            generated_at TEXT NOT NULL,
            price_session_start TEXT,
            price_session_end TEXT,
            strategy_mode TEXT NOT NULL,
            strategy_name TEXT,
            regime_name TEXT,
            strategy_reason TEXT,
            account_snapshot TEXT NOT NULL,
            cash_after REAL,
            remaining_capacity_before REAL,
            remaining_capacity_after REAL,
            no_purchase_explanation TEXT,
            price_source TEXT,
            data_warnings TEXT NOT NULL DEFAULT '[]',
            needs_regeneration INTEGER NOT NULL DEFAULT 0,
            stale_reason TEXT
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS plan_recommendations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plan_id INTEGER NOT NULL,
            section TEXT NOT NULL,
            rank INTEGER,
            ticker TEXT NOT NULL,
            action TEXT NOT NULL,
            signal TEXT,
            score REAL,
            price REAL,
            price_as_of TEXT,
            quantity REAL,
            allocation REAL,
            reason TEXT NOT NULL DEFAULT '',
            position_id INTEGER,
            direction TEXT,
            status TEXT NOT NULL DEFAULT 'OPEN',
            dismissal_note TEXT,
            executed_trade_kind TEXT,
            executed_trade_id INTEGER,
            executed_at TEXT
        )
        """
    )
    columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(trading_plans)").fetchall()
    }
    if "needs_regeneration" not in columns:
        connection.execute(
            "ALTER TABLE trading_plans ADD COLUMN needs_regeneration INTEGER NOT NULL DEFAULT 0"
        )
    if "stale_reason" not in columns:
        connection.execute("ALTER TABLE trading_plans ADD COLUMN stale_reason TEXT")
    if "data_warnings" not in columns:
        connection.execute(
            "ALTER TABLE trading_plans ADD COLUMN data_warnings TEXT NOT NULL DEFAULT '[]'"
        )


def _connect(db_path=None):
    from paper_trading import storage

    return storage._connect(db_path)


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def collect_data_warnings(plan):
    warnings = list(plan.get("data_warnings") or [])
    for ticker in plan.get("unavailable_universe") or []:
        message = f"{ticker}: price history unavailable; not treated as a sell."
        if message not in warnings:
            warnings.append(message)
    for item in plan.get("skipped_buys") or []:
        message = f"{item.get('Ticker')}: {item.get('Reason')}"
        if message not in warnings:
            warnings.append(message)
    cache = plan.get("cache_info") or {}
    for failure in cache.get("Data Source Failures") or []:
        message = (
            f"{failure.get('Ticker')}: {failure.get('Reason')} "
            f"({failure.get('Requested Start')} to {failure.get('Requested End')})"
        )
        if message not in warnings:
            warnings.append(message)
    return warnings


def save_plan(plan, account_state, open_positions, db_path=None):
    """Insert a new plan version. Does not update or delete older plans."""
    from paper_trading import storage

    storage.initialize_storage(db_path)
    snapshot = {
        "Cash": account_state["Cash"],
        "Starting Capital": account_state["Starting Capital"],
        "Realized P&L": account_state["Realized P&L"],
        "Open Position IDs": [position["id"] for position in open_positions or []],
    }
    warnings = collect_data_warnings(plan)
    connection = _connect(db_path)
    try:
        cursor = connection.execute(
            """
            INSERT INTO trading_plans (
                generated_at, price_session_start, price_session_end,
                strategy_mode, strategy_name, regime_name, strategy_reason,
                account_snapshot, cash_after, remaining_capacity_before,
                remaining_capacity_after, no_purchase_explanation, price_source,
                data_warnings, needs_regeneration, stale_reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL)
            """,
            (
                _now_iso(),
                plan.get("price_session_start"),
                plan.get("price_session_end"),
                plan.get("mode") or "",
                plan.get("strategy_name"),
                plan.get("regime_name"),
                plan.get("strategy_reason") or "",
                json.dumps(snapshot),
                plan.get("cash_after"),
                plan.get("remaining_capacity_before"),
                plan.get("remaining_capacity_after"),
                plan.get("no_purchase_explanation"),
                plan.get("price_source"),
                json.dumps(warnings),
            ),
        )
        plan_id = cursor.lastrowid
        for rank, row in enumerate(plan.get("buys") or [], start=1):
            _insert_recommendation(
                connection,
                plan_id,
                SECTION_BUY,
                rank,
                ticker=row["Ticker"],
                action="BUY",
                signal=row.get("Signal"),
                score=row.get("Score"),
                price=row.get("Price"),
                price_as_of=row.get("Price As Of"),
                quantity=row.get("Quantity"),
                allocation=row.get("Allocation"),
                reason=row.get("Reason") or "",
                position_id=None,
                direction="LONG",
            )
        for rank, row in enumerate(plan.get("holdings") or [], start=1):
            _insert_recommendation(
                connection,
                plan_id,
                SECTION_HOLDING,
                rank,
                ticker=row.get("ticker"),
                action=row.get("Action") or UNAVAILABLE,
                signal=row.get("Signal"),
                score=row.get("Score"),
                price=row.get("Price"),
                price_as_of=row.get("Price As Of"),
                quantity=row.get("quantity"),
                allocation=row.get("allocated_capital"),
                reason=row.get("Reason") or "",
                position_id=row.get("id"),
                direction=row.get("direction"),
            )
        connection.commit()
    finally:
        connection.close()
    return plan_id


def _insert_recommendation(connection, plan_id, section, rank, **fields):
    connection.execute(
        """
        INSERT INTO plan_recommendations (
            plan_id, section, rank, ticker, action, signal, score, price,
            price_as_of, quantity, allocation, reason, position_id, direction,
            status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            plan_id,
            section,
            rank,
            fields["ticker"],
            fields["action"],
            fields["signal"],
            fields["score"],
            fields["price"],
            fields["price_as_of"],
            fields["quantity"],
            fields["allocation"],
            fields["reason"],
            fields["position_id"],
            fields["direction"],
            STATUS_OPEN,
        ),
    )


def list_saved_plans(db_path=None):
    connection = _connect(db_path)
    try:
        try:
            rows = connection.execute(
                """
                SELECT id, generated_at, price_session_start, price_session_end,
                       strategy_name, regime_name, needs_regeneration
                FROM trading_plans
                ORDER BY id DESC
                """
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []
    finally:
        connection.close()
    return [dict(row) for row in rows]


def load_latest_plan(db_path=None):
    plans = list_saved_plans(db_path)
    if not plans:
        return None
    return load_plan(plans[0]["id"], db_path=db_path)


def load_plan(plan_id, db_path=None):
    connection = _connect(db_path)
    try:
        row = connection.execute(
            "SELECT * FROM trading_plans WHERE id = ?", (plan_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"No saved trading plan with id {plan_id}.")
        recommendations = connection.execute(
            """
            SELECT * FROM plan_recommendations
            WHERE plan_id = ?
            ORDER BY section ASC, rank ASC, id ASC
            """,
            (plan_id,),
        ).fetchall()
    finally:
        connection.close()
    plan = dict(row)
    snapshot = json.loads(plan["account_snapshot"])
    warnings = json.loads(plan["data_warnings"] or "[]")
    buys = []
    holdings = []
    for item in recommendations:
        record = dict(item)
        if record["section"] == SECTION_BUY:
            buys.append(
                {
                    "recommendation_id": record["id"],
                    "Ticker": record["ticker"],
                    "Signal": record["signal"],
                    "Score": record["score"],
                    "Reason": record["reason"],
                    "Price": record["price"],
                    "Price As Of": record["price_as_of"],
                    "Quantity": record["quantity"],
                    "Allocation": record["allocation"],
                    "status": record["status"],
                    "dismissal_note": record["dismissal_note"],
                    "executed_trade_kind": record["executed_trade_kind"],
                    "executed_trade_id": record["executed_trade_id"],
                }
            )
        else:
            holdings.append(
                {
                    "recommendation_id": record["id"],
                    "id": record["position_id"],
                    "ticker": record["ticker"],
                    "direction": record["direction"],
                    "Action": record["action"],
                    "Signal": record["signal"],
                    "Score": record["score"],
                    "Reason": record["reason"],
                    "Price": record["price"],
                    "Price As Of": record["price_as_of"],
                    "quantity": record["quantity"],
                    "allocated_capital": record["allocation"],
                    "status": record["status"],
                    "dismissal_note": record["dismissal_note"],
                    "executed_trade_kind": record["executed_trade_kind"],
                    "executed_trade_id": record["executed_trade_id"],
                }
            )
    return {
        "id": plan["id"],
        "generated_at": plan["generated_at"],
        "price_session_start": plan["price_session_start"],
        "price_session_end": plan["price_session_end"],
        "mode": plan["strategy_mode"],
        "strategy_name": plan["strategy_name"],
        "regime_name": plan["regime_name"],
        "strategy_reason": plan["strategy_reason"],
        "account_snapshot": snapshot,
        "cash_after": plan["cash_after"],
        "cash_before": snapshot.get("Cash"),
        "remaining_capacity_before": plan["remaining_capacity_before"],
        "remaining_capacity_after": plan["remaining_capacity_after"],
        "no_purchase_explanation": plan["no_purchase_explanation"],
        "price_source": plan["price_source"],
        "data_warnings": warnings,
        "needs_regeneration": bool(plan["needs_regeneration"]),
        "stale_reason": plan["stale_reason"],
        "buys": buys,
        "holdings": holdings,
        "skipped_buys": [],
        "unavailable_universe": [],
        "recommendation": None,
    }


def previous_plan(plan_id, db_path=None):
    connection = _connect(db_path)
    try:
        row = connection.execute(
            """
            SELECT id FROM trading_plans
            WHERE id < ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (plan_id,),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        return None
    return load_plan(row["id"], db_path=db_path)


def compare_plans(previous, current):
    """Diff two saved plans. A dropped buy candidate is not a sell signal."""
    if previous is None or current is None:
        return {
            "new_candidates": [],
            "dropped_candidates": [],
            "signal_changes": [],
            "dropped_candidate_note": DROPPED_CANDIDATE_NOTE,
        }
    previous_buys = {row["Ticker"]: row for row in previous.get("buys") or []}
    current_buys = {row["Ticker"]: row for row in current.get("buys") or []}
    new_candidates = [
        current_buys[ticker] for ticker in current_buys if ticker not in previous_buys
    ]
    dropped_candidates = [
        previous_buys[ticker] for ticker in previous_buys if ticker not in current_buys
    ]

    def _holding_key(row):
        if row.get("id") is not None:
            return ("position", row["id"])
        return ("ticker", row.get("ticker"), row.get("direction"))

    previous_holdings = {_holding_key(row): row for row in previous.get("holdings") or []}
    signal_changes = []
    for row in current.get("holdings") or []:
        prior = previous_holdings.get(_holding_key(row))
        if prior is None:
            continue
        if prior.get("Signal") != row.get("Signal") or prior.get("Action") != row.get("Action"):
            signal_changes.append(
                {
                    "Ticker": row.get("ticker"),
                    "Position ID": row.get("id"),
                    "Previous Signal": prior.get("Signal"),
                    "Current Signal": row.get("Signal"),
                    "Previous Action": prior.get("Action"),
                    "Current Action": row.get("Action"),
                }
            )
    return {
        "new_candidates": new_candidates,
        "dropped_candidates": dropped_candidates,
        "signal_changes": signal_changes,
        "dropped_candidate_note": DROPPED_CANDIDATE_NOTE,
    }


def describe_plan_age(plan, now=None):
    """How old the saved generation and its daily-close session are."""
    now = now or datetime.now(timezone.utc)
    generated = datetime.fromisoformat(plan["generated_at"])
    if generated.tzinfo is None:
        generated = generated.replace(tzinfo=timezone.utc)
    age_hours = (now - generated).total_seconds() / 3600.0
    session_end = plan.get("price_session_end") or "unknown"
    return (
        f"Plan #{plan['id']} generated {plan['generated_at']} "
        f"({age_hours:.1f} hours ago). "
        f"Daily-close session {plan.get('price_session_start') or session_end}"
        f" to {session_end}. Not a real-time quote."
    )


def detect_plan_staleness(plan, account_state, open_positions):
    """True when the plan must be regenerated before another trade is recorded."""
    if plan.get("needs_regeneration"):
        return True, plan.get("stale_reason") or "This plan needs to be regenerated."
    snapshot = plan.get("account_snapshot") or {}
    if snapshot.get("Cash") is None:
        return True, "This plan has no account snapshot."
    if abs(float(snapshot["Cash"]) - float(account_state["Cash"])) > 1e-6:
        return True, "Cash no longer matches the snapshot used to build this plan."
    snapshot_ids = set(snapshot.get("Open Position IDs") or [])
    current_ids = {position["id"] for position in open_positions or []}
    if snapshot_ids != current_ids:
        return True, "Open positions no longer match the snapshot used to build this plan."
    return False, None


def _load_recommendation(connection, recommendation_id):
    row = connection.execute(
        "SELECT * FROM plan_recommendations WHERE id = ?", (recommendation_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"No recommendation with id {recommendation_id}.")
    return dict(row)


def dismiss_recommendation(recommendation_id, note="", db_path=None):
    """Mark a recommendation dismissed. This is not a trade and does not stale the plan."""
    connection = _connect(db_path)
    try:
        cursor = connection.execute(
            """
            UPDATE plan_recommendations
            SET status = ?, dismissal_note = ?
            WHERE id = ? AND status = ?
            """,
            (STATUS_DISMISSED, note or "", recommendation_id, STATUS_OPEN),
        )
        if cursor.rowcount != 1:
            current = _load_recommendation(connection, recommendation_id)
            raise DuplicateRecommendationError(
                f"Recommendation #{recommendation_id} is {current['status']} "
                "and cannot be dismissed again."
            )
        connection.commit()
    finally:
        connection.close()


def execute_recommendation(recommendation_id, fetch_snapshot_fn, db_path=None):
    """Record one OPEN recommendation through the existing ledger functions.

    Claims the row before calling open/close so a second click or rerun cannot
    insert another trade. Refreshes execution prices inside those functions'
    callers via `fetch_snapshot_fn`.
    """
    from paper_trading import storage

    connection = _connect(db_path)
    try:
        recommendation = _load_recommendation(connection, recommendation_id)
        plan_row = connection.execute(
            "SELECT * FROM trading_plans WHERE id = ?", (recommendation["plan_id"],)
        ).fetchone()
        latest = connection.execute(
            "SELECT id FROM trading_plans ORDER BY id DESC LIMIT 1"
        ).fetchone()
        plan_row = None if plan_row is None else dict(plan_row)
        latest_id = None if latest is None else latest["id"]
        if plan_row is None or latest_id is None or plan_row["id"] != latest_id:
            raise StalePlanError(
                "Only the latest saved plan can be executed. Open that plan or generate a new one."
            )
        if recommendation["status"] != STATUS_OPEN:
            raise DuplicateRecommendationError(
                f"Recommendation #{recommendation_id} is already {recommendation['status']}."
            )
        if plan_row["needs_regeneration"]:
            raise StalePlanError(
                plan_row["stale_reason"]
                or "This plan's account snapshot changed. Generate a new plan before recording another trade."
            )
        if recommendation["action"] not in ("BUY", REVIEW_EXIT):
            raise ValueError(
                f"{recommendation['action']} is not a paper trade. "
                "KEEP stays open, and UNAVAILABLE is not an exit."
            )
        claimed = connection.execute(
            """
            UPDATE plan_recommendations
            SET status = ?
            WHERE id = ? AND status = ?
            """,
            (STATUS_EXECUTING, recommendation_id, STATUS_OPEN),
        )
        if claimed.rowcount != 1:
            raise DuplicateRecommendationError(
                f"Recommendation #{recommendation_id} was already claimed."
            )
        connection.commit()
    finally:
        connection.close()

    try:
        holdings = storage.get_open_positions(db_path)
        required = [recommendation["ticker"]]
        required.extend(position["ticker"] for position in holdings)
        snapshot = fetch_snapshot_fn(list(dict.fromkeys(required)))
        quote = snapshot["quotes"][recommendation["ticker"]]
        if recommendation["action"] == "BUY":
            trade_id = storage.open_position(
                ticker=recommendation["ticker"],
                entry_price=quote["price"],
                allocated_capital=recommendation["allocation"],
                strategy=plan_row["strategy_name"] or "Manual",
                reason=(
                    f"Plan #{plan_row['id']} recommendation #{recommendation_id}: "
                    f"{recommendation['reason']}"
                ),
                direction="LONG",
                current_prices=snapshot["prices"],
                db_path=db_path,
            )
            trade_kind = "OPEN"
        else:
            if recommendation["position_id"] is None:
                raise ValueError(
                    f"{recommendation['ticker']} has no open position id to close."
                )
            _pnl, trade_id = storage.close_position(
                recommendation["position_id"],
                quote["price"],
                return_id=True,
                db_path=db_path,
            )
            trade_kind = "CLOSE"
    except Exception:
        _release_claim(recommendation_id, db_path)
        raise

    connection = _connect(db_path)
    try:
        connection.execute(
            """
            UPDATE plan_recommendations
            SET status = ?, executed_trade_kind = ?, executed_trade_id = ?, executed_at = ?
            WHERE id = ? AND status = ?
            """,
            (
                STATUS_EXECUTED,
                trade_kind,
                trade_id,
                _now_iso(),
                recommendation_id,
                STATUS_EXECUTING,
            ),
        )
        connection.commit()
    finally:
        connection.close()
    return {"trade_kind": trade_kind, "trade_id": trade_id, "execution_price": quote["price"]}


def _release_claim(recommendation_id, db_path):
    connection = _connect(db_path)
    try:
        connection.execute(
            """
            UPDATE plan_recommendations
            SET status = ?
            WHERE id = ? AND status = ? AND executed_trade_id IS NULL
            """,
            (STATUS_OPEN, recommendation_id, STATUS_EXECUTING),
        )
        connection.commit()
    finally:
        connection.close()
