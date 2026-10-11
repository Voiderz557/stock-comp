"""Read-only daily-close watcher for the local paper-trading dashboard.

Checks never build plans, mutate the paper ledger, or place trades. Only the
watcher's separate SQLite observation/alert history is written. A check is
performed on demand or while the dashboard is open with auto-checks enabled.
"""

from __future__ import annotations

import json
import math
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from paper_trading import storage
from paper_trading.plan_store import (
    compare_plans,
    detect_plan_staleness,
    load_latest_plan,
    previous_plan,
)
from paper_trading.portfolio import compute_exposure_summary
from paper_trading.prices import (
    SOURCE_LABEL,
    completed_daily_close_frame,
    latest_completed_close_date,
    latest_daily_close_quote,
)
from paper_trading.trading_plan import analyze_ticker, calendar_days_for_trading_days
from strategies.registry import get_strategy


PRICE_MOVE_THRESHOLD = 0.05
VOLUME_RATIO_THRESHOLD = 2.0
STALE_CLOSE_CALENDAR_DAYS = 4
AUTO_CHECK_INTERVAL = "15m"
HISTORY_LIMIT = 100


def watcher_db_path(paper_db_path=None):
    """Keep watcher history beside, but never inside, the paper ledger."""
    return storage.paper_data_directory(paper_db_path) / "market_watcher.sqlite3"


def _connect(db_path):
    connection = sqlite3.connect(db_path, timeout=10)
    connection.row_factory = sqlite3.Row
    return connection


def _ensure_schema(connection):
    connection.execute(
        "CREATE TABLE IF NOT EXISTS observations ("
        "id INTEGER PRIMARY KEY, checked_at TEXT NOT NULL, payload TEXT NOT NULL)"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS alerts ("
        "id INTEGER PRIMARY KEY, event_key TEXT NOT NULL UNIQUE, "
        "checked_at TEXT NOT NULL, severity TEXT NOT NULL, "
        "code TEXT NOT NULL, ticker TEXT, price_as_of TEXT, message TEXT NOT NULL)"
    )


def load_watcher_history(db_path=None, limit=HISTORY_LIMIT):
    """Read without creating a database when no watcher check has run."""
    path = Path(db_path) if db_path is not None else watcher_db_path()
    if not path.is_file():
        return {"last_observation": None, "alerts": []}
    with closing(_connect(path)) as connection:
        try:
            row = connection.execute(
                "SELECT checked_at, payload FROM observations ORDER BY id DESC LIMIT 1"
            ).fetchone()
            alerts = connection.execute(
                "SELECT checked_at, severity, code, ticker, price_as_of, message "
                "FROM alerts ORDER BY id DESC LIMIT ?", (max(0, int(limit)),)
            ).fetchall()
        except sqlite3.OperationalError:
            return {"last_observation": None, "alerts": []}
    return {
        "last_observation": None if row is None else json.loads(row["payload"]),
        "alerts": [dict(item) for item in alerts],
    }


def _alert(code, key, message, *, ticker=None, price_as_of=None, severity="notice"):
    return {
        "event_key": f"{code}:{key}",
        "code": code,
        "ticker": ticker,
        "price_as_of": price_as_of,
        "severity": severity,
        "message": message,
    }


def _latest_market_row(frame):
    quote = latest_daily_close_quote(frame)
    if quote is None:
        return None
    closes = frame["Close"].dropna()
    previous_close = float(closes.iloc[-2]) if len(closes) >= 2 else None
    change = (
        quote["price"] / previous_close - 1
        if previous_close is not None and math.isfinite(previous_close) and previous_close > 0
        else None
    )
    if change is not None and not math.isfinite(change):
        change = None
    ratio = None
    if "Volume" in frame and len(frame) >= 20:
        volumes = pd.to_numeric(frame["Volume"], errors="coerce").iloc[-20:]
        recent = volumes.iloc[-5:].mean()
        baseline = volumes.mean()
        if pd.notna(recent) and pd.notna(baseline) and baseline > 0:
            ratio = float(recent / baseline)
            if not math.isfinite(ratio):
                ratio = None
    return {
        "price": quote["price"],
        "price_as_of": quote["as_of"],
        "daily_change": change,
        "volume_ratio": ratio,
    }


def _asof_frame(frame, now):
    """Restrict indicator input to completed sessions as of the check."""
    return completed_daily_close_frame(frame, now)


def evaluate_observation(
    account, positions, plan, prior_plan, price_frames, previous=None, now=None
):
    """Detect paper-only events from completed daily bars available at `now`."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    price_frames = {
        ticker: _asof_frame(frame, now)
        for ticker, frame in (price_frames or {}).items()
    }
    previous = previous or {}
    plan_id = plan.get("id") if plan else None
    watched = sorted(
        {row["ticker"] for row in positions}
        | {row["Ticker"] for row in (plan or {}).get("buys", [])}
    )
    definition = None
    strategy_name = (plan or {}).get("strategy_name")
    if strategy_name:
        try:
            candidate = get_strategy(strategy_name)
            if candidate.live_enabled:
                definition = candidate
        except ValueError:
            pass
    snapshot = {
        "checked_at": now.isoformat(),
        "price_source": SOURCE_LABEL,
        "plan_id": plan_id,
        "plan_generated_at": (plan or {}).get("generated_at"),
        "strategy_name": strategy_name,
        "tickers": {},
        "watched_tickers": watched,
    }
    alerts = []
    if plan and previous.get("plan_id") != plan_id:
        diff = compare_plans(prior_plan, plan)
        new = [row["Ticker"] for row in diff["new_candidates"]]
        dropped = [row["Ticker"] for row in diff["dropped_candidates"]]
        changes = [row["Ticker"] for row in diff["signal_changes"]]
        alerts.append(_alert(
            "plan_changed", plan_id,
            f"Saved plan #{plan_id} is now current. New buys: {', '.join(new) or 'none'}; "
            f"dropped buys (not sell signals): {', '.join(dropped) or 'none'}; "
            f"holding signal changes: {', '.join(changes) or 'none'}."
        ))
    if plan:
        stale, reason = detect_plan_staleness(plan, account, positions)
        if stale:
            alerts.append(_alert(
                "stale_plan", f"{plan_id}:{reason}",
                reason or "Saved plan is stale; regenerate before recording a recommendation.",
                severity="warning",
            ))
        session_end = plan.get("price_session_end")
        if session_end:
            try:
                session_age = (now.date() - pd.Timestamp(session_end).date()).days
            except (TypeError, ValueError):
                session_age = None
            if session_age is None or session_age > STALE_CLOSE_CALENDAR_DAYS:
                alerts.append(_alert(
                    "old_plan", f"{plan_id}:{session_end}",
                    f"Saved plan #{plan_id} uses daily closes through {session_end}; "
                    "review or regenerate before acting.",
                    severity="warning",
                ))
        else:
            alerts.append(_alert(
                "plan_date_unknown", plan_id,
                f"Saved plan #{plan_id} has no price-session end date; freshness is unknown.",
                severity="warning",
            ))
        for warning in plan.get("data_warnings") or []:
            alerts.append(_alert(
                "plan_data_gap", f"{plan_id}:{warning}",
                f"Saved plan data gap: {warning}", severity="warning"
            ))
    prices = {}
    for ticker in watched:
        frame = (price_frames or {}).get(ticker)
        row = _latest_market_row(frame)
        if row is None:
            snapshot["tickers"][ticker] = {"price": None, "price_as_of": None, "signal": None}
            alerts.append(_alert(
                "missing_price", f"{ticker}:{now.date()}",
                f"{ticker}: no valid daily close; exposure and signal changes cannot be verified.",
                ticker=ticker, severity="warning",
            ))
            continue
        as_of = row["price_as_of"]
        quote_date = pd.Timestamp(as_of)
        if quote_date > pd.Timestamp(now):
            snapshot["tickers"][ticker] = {"price": None, "price_as_of": None, "signal": None}
            alerts.append(_alert(
                "future_price", f"{ticker}:{as_of}",
                f"{ticker}: returned close is later than this check; ignored.",
                ticker=ticker, severity="warning",
            ))
            continue
        signal = None
        if definition:
            analysis = analyze_ticker(definition, ticker, price_frames)
            signal = analysis.get("Signal") if analysis else None
            if signal is None:
                alerts.append(_alert(
                    "signal_unavailable", f"{ticker}:{as_of}:{strategy_name}",
                    f"{ticker}: {strategy_name} signal unavailable with current history; no signal inferred.",
                    ticker=ticker, price_as_of=as_of, severity="warning",
                ))
        row["signal"] = signal
        snapshot["tickers"][ticker] = row
        prices[ticker] = row["price"]
        if (now - quote_date.to_pydatetime()).days > STALE_CLOSE_CALENDAR_DAYS:
            alerts.append(_alert(
                "old_price", f"{ticker}:{as_of}",
                f"{ticker}: latest daily close is from {as_of}; it may be stale.",
                ticker=ticker, price_as_of=as_of, severity="warning",
            ))
        if row["daily_change"] is not None and abs(row["daily_change"]) >= PRICE_MOVE_THRESHOLD:
            alerts.append(_alert(
                "price_move", f"{ticker}:{as_of}",
                f"{ticker}: latest close moved {row['daily_change']:+.1%} from the preceding close.",
                ticker=ticker, price_as_of=as_of,
            ))
        if row["volume_ratio"] is not None and row["volume_ratio"] >= VOLUME_RATIO_THRESHOLD:
            alerts.append(_alert(
                "volume_surge", f"{ticker}:{as_of}",
                f"{ticker}: recent 5-day average volume is {row['volume_ratio']:.1f}× the 20-day average.",
                ticker=ticker, price_as_of=as_of,
            ))
        old = (previous.get("tickers") or {}).get(ticker) or {}
        if (
            previous.get("strategy_name") == strategy_name
            and old.get("signal") and signal and old["signal"] != signal
        ):
            alerts.append(_alert(
                "signal_change", f"{ticker}:{strategy_name}:{old['signal']}:{signal}:{as_of}",
                f"{ticker}: {strategy_name} signal changed {old['signal']} → {signal}.",
                ticker=ticker, price_as_of=as_of,
            ))
    if positions:
        try:
            capacity = compute_exposure_summary(positions, prices, account["Starting Capital"])
            if capacity["Remaining Capacity"] <= 0:
                alerts.append(_alert(
                    "capacity_full", f"{now.date()}:{plan_id}",
                    "No shared gross-exposure capacity remains for new paper positions.",
                    severity="warning",
                ))
        except ValueError as error:
            alerts.append(_alert(
                "capacity_unavailable", f"{now.date()}:{plan_id}",
                f"Cannot verify remaining capacity: {error}", severity="warning",
            ))
    if account["Cash"] <= 0:
        alerts.append(_alert(
            "cash_full", f"{now.date()}:{plan_id}",
            "No paper cash remains for new positions.", severity="warning",
        ))
    return snapshot, alerts


def run_watcher_check(*, paper_db_path=None, watcher_path=None, load_market_data_fn=None, now=None):
    """Take one bounded read-only paper-account check and persist watcher history."""
    if load_market_data_fn is None:
        from data.market_data import load_market_data as load_market_data_fn
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    account = storage.get_account_state(paper_db_path)
    positions = storage.get_open_positions(paper_db_path)
    plan = load_latest_plan(paper_db_path)
    prior = previous_plan(plan["id"], db_path=paper_db_path) if plan else None
    path = Path(watcher_path) if watcher_path is not None else watcher_db_path(paper_db_path)
    previous = load_watcher_history(path)["last_observation"]
    tickers = sorted(
        {row["ticker"] for row in positions}
        | {row["Ticker"] for row in (plan or {}).get("buys", [])}
    )
    definition = None
    if plan and plan.get("strategy_name"):
        try:
            definition = get_strategy(plan["strategy_name"])
        except ValueError:
            pass
    required_days = max(20, definition.required_history_days if definition else 20)
    benchmark_ticker = getattr(definition, "benchmark_ticker", None)
    fetch_tickers = sorted(set(tickers) | ({benchmark_ticker} if benchmark_ticker and tickers else set()))
    frames = {}
    data_failures = []
    if fetch_tickers:
        # The market-data loader and listing registry use naive session dates.
        # Avoid fetching a partial bar while the New York session is open.
        end = pd.Timestamp(latest_completed_close_date(now))
        start = end - pd.Timedelta(days=calendar_days_for_trading_days(required_days + 2))
        frames, cache_info = load_market_data_fn(fetch_tickers, start, end)
        data_failures = (cache_info or {}).get("Data Source Failures") or []
        # Even if a provider returns future rows, never analyze beyond the check time.
        frames = {ticker: _asof_frame(frame, now) for ticker, frame in (frames or {}).items()}
    if (
        storage.get_account_state(paper_db_path) != account
        or storage.get_open_positions(paper_db_path) != positions
        or (load_latest_plan(paper_db_path) or {}).get("id") != (plan or {}).get("id")
    ):
        raise RuntimeError("Paper account or saved plan changed during the watcher check; retry with a fresh snapshot.")
    snapshot, alerts = evaluate_observation(account, positions, plan, prior, frames, previous, now)
    for failure in data_failures:
        ticker = failure.get("Ticker")
        alerts.append(_alert(
            "provider_gap", f"{ticker}:{now.date()}:{failure.get('Reason')}",
            f"{ticker}: data provider reported a gap; {failure.get('Reason')}. No replacement price inferred.",
            ticker=ticker, severity="warning",
        ))
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(_connect(path)) as connection:
        with connection:
            _ensure_schema(connection)
            connection.execute(
                "INSERT INTO observations (checked_at, payload) VALUES (?, ?)",
                (snapshot["checked_at"], json.dumps(snapshot, allow_nan=False)),
            )
            new_alerts = []
            for alert in alerts:
                cursor = connection.execute(
                    "INSERT OR IGNORE INTO alerts "
                    "(event_key, checked_at, severity, code, ticker, price_as_of, message) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (alert["event_key"], snapshot["checked_at"], alert["severity"],
                     alert["code"], alert["ticker"], alert["price_as_of"], alert["message"]),
                )
                if cursor.rowcount:
                    new_alerts.append(alert)
    return {"observation": snapshot, "new_alerts": new_alerts, "data_failures": data_failures}
