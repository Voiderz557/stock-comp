"""Live paper-trading competition dashboard.

This page is the day-to-day "cockpit" for the competition: it shows the
current simulated portfolio, the current market regime, and ranked BUY
candidates from every registered strategy, and lets a human open/close
*simulated* positions.

Hard boundaries respected by this file:
- No real orders are ever placed. Every position here is a row persisted
  by `paper_trading.storage` to a local SQLite database.
- Strategy logic is never modified - this file only calls the public
  `analyze()` / `rank_key()` interface every strategy already exposes via
  `strategies.registry`.
- The backtesting engine (`backtesting/`) is not imported or touched here.
"""

import pandas as pd
import streamlit as st

from config import MAX_POSITION_VALUE, MIN_STOCK_PRICE, STOCK_UNIVERSE
from data.market_data import load_market_data
from market.regime import load_current_market_regime
from market.strategy_selector import describe_availability, recommend_strategy
from paper_trading import storage as paper_storage
from paper_trading.ledger import FAIL, INSOLVENCY, MARKET_BREACH, PASS, UNAVAILABLE
from paper_trading.portfolio import (
    latest_close_prices,
    scan_for_candidates,
    summarize_portfolio,
)
from paper_trading.prices import SOURCE_LABEL, fetch_execution_snapshot
from paper_trading.plan_store import (
    DROPPED_CANDIDATE_NOTE,
    compare_plans,
    describe_plan_age,
    detect_plan_staleness,
    dismiss_recommendation,
    execute_recommendation,
    list_saved_plans,
    load_plan,
    previous_plan,
    save_plan,
)
from ai.settings import ai_ui_enabled
from ai.ui import render_ai_analysis_section
from paper_trading.trading_plan import (
    AUTOMATIC_MODE,
    KEEP,
    REVIEW_EXIT,
    load_and_build_trading_plan,
    plan_mode_options,
    plan_to_csv,
)
from strategies.registry import (
    available_strategy_names,
    extra_strategy_benchmark_tickers,
    get_strategy,
)

try:
    from app.account_setup_ui import render_account_setup_section
except ImportError:
    from account_setup_ui import render_account_setup_section


# ---------------------------------------------------------------------------
# Tunable, readable constants (not competition constraints - purely how much
# history this page fetches for its own display purposes).
# ---------------------------------------------------------------------------
TRADING_TO_CALENDAR_DAY_MULTIPLIER = 1.6
SCAN_HISTORY_BUFFER_CALENDAR_DAYS = 30


def _calendar_days_for_trading_days(trading_days):
    return int(trading_days * TRADING_TO_CALENDAR_DAY_MULTIPLIER) + SCAN_HISTORY_BUFFER_CALENDAR_DAYS


st.set_page_config(page_title="Paper Trading Competition Dashboard", layout="wide")
st.title("Paper Trading Competition Dashboard")
st.caption(
    "Every position on this page is simulated (paper trading only) and "
    "persisted to a local database. Nothing here places a real order. "
    "Recommendations are not trades until you record them."
)
st.caption(f"Saved portfolio and plans: `{paper_storage.paper_data_directory()}`")

paper_storage.initialize_storage()

render_account_setup_section()


# ---------------------------------------------------------------------------
# Portfolio Overview
# ---------------------------------------------------------------------------
st.header("Portfolio Overview")

account_state = paper_storage.get_account_state()
open_positions = paper_storage.get_open_positions()
closed_trades = paper_storage.get_closed_trades()

position_tickers = sorted({position["ticker"] for position in open_positions})
current_prices = {}
price_quotes = {}
if position_tickers:
    try:
        snapshot = fetch_execution_snapshot(position_tickers, load_market_data)
        current_prices = snapshot["prices"]
        price_quotes = snapshot["quotes"]
    except Exception as error:
        st.warning(
            f"Could not refresh latest daily closes for open positions: {error}"
        )

st.caption(
    f"Holdings and new executions use the {SOURCE_LABEL}. "
    "This is not a real-time quote feed."
)
if account_state.get("Snapshot As Of"):
    st.caption(f"Competition account snapshot date: {account_state['Snapshot As Of']}")
if price_quotes:
    as_of_bits = [
        f"{ticker} close as of {quote['as_of']}"
        for ticker, quote in sorted(price_quotes.items())
    ]
    st.caption("Price timestamps (UTC): " + "; ".join(as_of_bits))

summary = summarize_portfolio(account_state, open_positions, current_prices)

metric_columns = st.columns(5)
metric_columns[0].metric("Portfolio Value", f"${summary['Portfolio Value']:,.2f}")
metric_columns[1].metric("Cash", f"${summary['Cash']:,.2f}")
metric_columns[2].metric("Unrealized P&L", f"${summary['Total Unrealized P&L']:+,.2f}")
metric_columns[3].metric("Realized P&L", f"${summary['Realized P&L']:+,.2f}")
starting_capital = summary["Starting Capital"] or 0.0
total_return = (
    (summary["Portfolio Value"] / starting_capital - 1) if starting_capital else 0.0
)
metric_columns[4].metric("Total Return", f"{total_return:+.2%}")

capacity_columns = st.columns(4)
capacity_columns[0].metric(
    "Starting Capital",
    f"${starting_capital:,.2f}" if summary["Starting Capital"] is not None else "n/a",
)
if summary["Capacity Available"]:
    capacity_columns[1].metric(
        "Current Long Exposure", f"${summary['Current Long Exposure']:,.2f}"
    )
    capacity_columns[2].metric(
        "Gross Exposure",
        f"${summary['Gross Exposure']:,.2f}",
        help="Current long market value + abs(current short market value). "
        "LONG and SHORT share this one ceiling.",
    )
    capacity_columns[3].metric(
        "Remaining Capacity",
        f"${summary['Remaining Capacity']:,.2f}",
        help="max(0, starting capital - gross exposure). New opens must fit "
        "both this and available cash.",
    )
    st.caption(
        f"Current short exposure ${summary['Current Short Exposure']:,.2f}; "
        f"net exposure ${summary['Net Exposure']:,.2f}. "
        "Market moves that push gross exposure above starting capital block "
        "new opens but never force a close."
    )
else:
    capacity_columns[1].metric("Current Long Exposure", "n/a")
    capacity_columns[2].metric("Gross Exposure", "n/a")
    capacity_columns[3].metric("Remaining Capacity", "n/a")
    if summary["Capacity Error"]:
        st.warning(summary["Capacity Error"])

saved_plan_index = list_saved_plans()
latest_saved_plan = load_plan(saved_plan_index[0]["id"]) if saved_plan_index else None
st.subheader("Latest saved plan")
if latest_saved_plan is None:
    st.info("No saved plan yet. Generate today's plan below. It will still be here after a restart.")
else:
    st.caption(describe_plan_age(latest_saved_plan))
    stale, stale_reason = detect_plan_staleness(
        latest_saved_plan, account_state, open_positions
    )
    if stale:
        st.warning(stale_reason or "This plan needs to be regenerated.")
    prior_plan = previous_plan(latest_saved_plan["id"])
    plan_changes = compare_plans(prior_plan, latest_saved_plan)
    buy_count = len(latest_saved_plan["buys"])
    keep_count = sum(1 for row in latest_saved_plan["holdings"] if row["Action"] == KEEP)
    exit_count = sum(
        1 for row in latest_saved_plan["holdings"] if row["Action"] == REVIEW_EXIT
    )
    plan_counts = st.columns(3)
    plan_counts[0].metric("Proposed buys", buy_count)
    plan_counts[1].metric("Holdings to keep", keep_count)
    plan_counts[2].metric("Exits to review", exit_count)
    if latest_saved_plan["buys"]:
        st.caption(
            "Proposed buys: "
            + ", ".join(row["Ticker"] for row in latest_saved_plan["buys"])
        )
    if keep_count:
        st.caption(
            "Keep: "
            + ", ".join(
                row["ticker"]
                for row in latest_saved_plan["holdings"]
                if row["Action"] == KEEP
            )
        )
    if exit_count:
        st.caption(
            "Review exit: "
            + ", ".join(
                row["ticker"]
                for row in latest_saved_plan["holdings"]
                if row["Action"] == REVIEW_EXIT
            )
        )
    st.markdown("**Changes since the previous plan**")
    st.caption(DROPPED_CANDIDATE_NOTE)
    if prior_plan is None:
        st.caption("No previous plan to compare.")
    else:
        change_columns = st.columns(3)
        change_columns[0].write(
            "**New candidates:** "
            + (
                ", ".join(row["Ticker"] for row in plan_changes["new_candidates"])
                or "none"
            )
        )
        change_columns[1].write(
            "**Dropped candidates (not sells):** "
            + (
                ", ".join(row["Ticker"] for row in plan_changes["dropped_candidates"])
                or "none"
            )
        )
        change_columns[2].write(
            "**Signal changes:** "
            + (
                ", ".join(
                    f"{row['Ticker']} {row['Previous Signal']} to {row['Current Signal']}"
                    for row in plan_changes["signal_changes"]
                )
                or "none"
            )
        )

audit = paper_storage.audit_ledger(current_prices)
st.subheader("Ledger audit")
audit_status = audit["Status"]
if audit["Accounting"] == FAIL:
    st.error(f"Accounting {FAIL}: stored ledger identities do not reconcile. Historical rows were not repaired.")
elif audit_status == UNAVAILABLE:
    st.warning(
        f"Valuation {UNAVAILABLE}: some audit checks could not run without "
        "a complete daily-close snapshot. Unavailable checks are not marked PASS."
    )
elif audit_status == MARKET_BREACH:
    st.info(
        "Gross exposure is above starting capital because prices moved. "
        "This is not an accounting error and does not force a close."
    )
elif audit["Solvency"] == INSOLVENCY:
    st.warning(
        "Equity is negative (for example after a large short loss). "
        "This is not an accounting error; new opens remain blocked if cash "
        "or capacity is insufficient."
    )
else:
    st.success("Accounting identities passed.")

audit_table = pd.DataFrame(audit["Checks"])
st.dataframe(audit_table, width="stretch", hide_index=True)
st.caption(
    f"Accounting={audit['Accounting']}; Valuation={audit['Valuation']}; "
    f"Solvency={audit['Solvency']}. Unavailable checks are never treated as PASS."
)

if ai_ui_enabled():
    render_ai_analysis_section(
        current_prices=current_prices,
        price_quotes=price_quotes,
    )

st.subheader("Open Positions")
if not summary["Open Positions"]:
    st.info("No open paper positions.")
else:
    positions_table = pd.DataFrame(summary["Open Positions"]).rename(
        columns={
            "id": "ID",
            "ticker": "Ticker",
            "direction": "Direction",
            "strategy": "Strategy",
            "entry_price": "Entry Price",
            "quantity": "Quantity",
            "allocated_capital": "Allocated Capital",
            "entry_timestamp": "Entry Time",
            "reason": "Reason",
        }
    )
    display_columns = [
        "ID",
        "Ticker",
        "Direction",
        "Strategy",
        "Entry Price",
        "Current Price",
        "Quantity",
        "Allocated Capital",
        "Market Value",
        "Current Exposure",
        "Unrealized P&L",
        "Unrealized P&L %",
        "Entry Time",
        "Reason",
        "Price Unavailable",
    ]
    display_columns = [column for column in display_columns if column in positions_table.columns]
    st.dataframe(
        positions_table[display_columns].style.format(
            {
                "Entry Price": "${:,.2f}",
                "Current Price": "${:,.2f}",
                "Quantity": "{:,.4f}",
                "Allocated Capital": "${:,.2f}",
                "Market Value": "${:,.2f}",
                "Current Exposure": "${:,.2f}",
                "Unrealized P&L": "${:+,.2f}",
                "Unrealized P&L %": "{:+.2%}",
            }
        ),
        width="stretch",
        hide_index=True,
    )

    st.subheader("Close a Position")
    position_labels = {
        f"#{position['id']} {position['ticker']} "
        f"({position['direction']}, {position['quantity']:.4f} sh)": position["id"]
        for position in summary["Open Positions"]
    }
    with st.form("close_position_form"):
        selected_label = st.selectbox("Position to close", options=list(position_labels))
        close_submitted = st.form_submit_button("Close at latest available price")
        if close_submitted:
            position_id = position_labels[selected_label]
            position = next(item for item in summary["Open Positions"] if item["id"] == position_id)
            try:
                close_snapshot = fetch_execution_snapshot(
                    [position["ticker"]], load_market_data
                )
                exit_quote = close_snapshot["quotes"][position["ticker"]]
                realized_pnl = paper_storage.close_position(
                    position_id, exit_quote["price"]
                )
                st.success(
                    f"Closed #{position_id} {position['ticker']} at "
                    f"${exit_quote['price']:,.2f} ({exit_quote['source_label']} "
                    f"as of {exit_quote['as_of']}; realized P&L "
                    f"${realized_pnl:+,.2f})."
                )
                st.rerun()
            except Exception as error:
                st.error(f"Could not close position: {error}")

st.subheader("Closed Trade History")
if not closed_trades:
    st.info("No closed paper trades yet.")
else:
    closed_table = pd.DataFrame(closed_trades).rename(
        columns={
            "id": "ID",
            "ticker": "Ticker",
            "direction": "Direction",
            "entry_price": "Entry Price",
            "exit_price": "Exit Price",
            "quantity": "Quantity",
            "allocated_capital": "Allocated Capital",
            "entry_timestamp": "Entry Time",
            "exit_timestamp": "Exit Time",
            "realized_pnl": "Realized P&L",
            "strategy": "Strategy",
            "reason": "Reason",
        }
    )
    st.dataframe(
        closed_table.style.format(
            {
                "Entry Price": "${:,.2f}",
                "Exit Price": "${:,.2f}",
                "Quantity": "{:,.4f}",
                "Allocated Capital": "${:,.2f}",
                "Realized P&L": "${:+,.2f}",
            }
        ),
        width="stretch",
        hide_index=True,
    )


# ---------------------------------------------------------------------------
# Market Regime
# ---------------------------------------------------------------------------
st.header("Market Regime")
st.caption(
    "Rule-based read on current SPY/QQQ conditions (see market/regime.py). "
    "Informational only - it does not place trades automatically."
)

if st.button("Detect current market regime"):
    try:
        regime_result, _cache_info = load_current_market_regime()
        st.session_state["regime_result"] = regime_result
    except Exception as error:
        st.session_state["regime_result"] = None
        st.error(f"Could not detect the current market regime: {error}")

regime_result = st.session_state.get("regime_result")
if regime_result is not None:
    recommendation = recommend_strategy(regime_result)

    regime_column, confidence_column = st.columns(2)
    regime_column.metric("Current Market Regime", regime_result.regime)
    confidence_column.metric("Confidence", f"{regime_result.confidence:.0%}")
    st.write(f"**Reason:** {regime_result.reason}")

    def _format_availability(strategy_names):
        pairs = describe_availability(strategy_names)
        if not pairs:
            return "None"
        return ", ".join(
            name if is_available else f"{name} (not yet implemented)"
            for name, is_available in pairs
        )

    st.write(f"**Preferred Strategies:** {_format_availability(recommendation.preferred_strategies)}")
    st.write(f"**Strategies to Avoid:** {_format_availability(recommendation.strategies_to_avoid)}")
    for note in recommendation.notes:
        st.info(note)

    with st.expander("Supporting metrics", expanded=False):
        features = regime_result.features
        metrics_table = pd.DataFrame(
            [
                {"Metric": "SPY Price", "Value": f"${features.spy_price:,.2f}"},
                {"Metric": "SPY MA50", "Value": f"${features.spy_ma50:,.2f}"},
                {"Metric": "SPY MA200", "Value": f"${features.spy_ma200:,.2f}"},
                {"Metric": "SPY Momentum 20D", "Value": f"{features.spy_momentum_20d:+.2%}"},
                {"Metric": "SPY Momentum 60D", "Value": f"{features.spy_momentum_60d:+.2%}"},
                {"Metric": "QQQ Price", "Value": f"${features.qqq_price:,.2f}"},
                {"Metric": "QQQ MA50", "Value": f"${features.qqq_ma50:,.2f}"},
                {"Metric": "QQQ MA200", "Value": f"${features.qqq_ma200:,.2f}"},
                {"Metric": "QQQ Momentum 20D", "Value": f"{features.qqq_momentum_20d:+.2%}"},
                {"Metric": "QQQ Momentum 60D", "Value": f"{features.qqq_momentum_60d:+.2%}"},
                {"Metric": "20D Market Volatility", "Value": f"{features.market_volatility_20d:.2%}"},
            ]
        )
        st.dataframe(metrics_table, width="stretch", hide_index=True)
        st.dataframe(
            pd.DataFrame(
                {
                    "Check": list(regime_result.supporting_metrics.keys()),
                    "Result": list(regime_result.supporting_metrics.values()),
                }
            ),
            width="stretch",
            hide_index=True,
        )
else:
    st.caption("Click the button above to detect the current market regime.")


# ---------------------------------------------------------------------------
# Today's Trading Plan (recommendation only)
# ---------------------------------------------------------------------------
st.header("Today's Trading Plan")
st.caption(
    "Recommendation only: building a plan never opens or closes positions. "
    f"Prices are the {SOURCE_LABEL}. Proposed REVIEW EXIT rows do not free "
    "cash or capacity. Record a paper trade from a recommendation below to "
    "refresh execution prices and recheck constraints. Manual open/close "
    "forms remain available for one-off lots."
)
plan_mode = st.selectbox(
    "Strategy for today's plan",
    options=plan_mode_options(),
    help=(
        f"{AUTOMATIC_MODE} uses the existing regime-to-strategy selector "
        "and its first available preferred strategy. It does not invent a "
        "new ranking formula."
    ),
)
if st.button("Build today's trading plan"):
    status_box = st.empty()
    progress = st.progress(0, text="Starting scan...")

    def _plan_status(stage, detail=""):
        text = f"{stage}: {detail}" if detail else stage
        status_box.info(text)
        if str(stage).lower().startswith("detect"):
            progress.progress(0.2, text=text)
        elif str(stage).lower().startswith("load"):
            progress.progress(0.55, text=text)
        else:
            progress.progress(0.85, text=text)

    try:
        account_for_plan = paper_storage.get_account_state()
        holdings_for_plan = paper_storage.get_open_positions()
        plan = load_and_build_trading_plan(
            plan_mode,
            account_for_plan,
            holdings_for_plan,
            universe=STOCK_UNIVERSE,
            load_market_data_fn=load_market_data,
            status_callback=_plan_status,
        )
        save_plan(plan, account_for_plan, holdings_for_plan)
        progress.progress(1.0, text="Plan saved")
        status_box.empty()
        st.rerun()
    except Exception as error:
        progress.progress(0)
        st.error(f"Could not build today's trading plan: {error}")

saved_plans = list_saved_plans()
if not saved_plans:
    plan = None
    st.caption("Choose a strategy and build a plan to see keep / review / buy suggestions.")
else:
    plan_labels = {
        (
            f"{item['generated_at'][:10]} #{item['id']} "
            f"{item['strategy_name'] or 'no strategy'}"
            + (" (needs regeneration)" if item["needs_regeneration"] else "")
        ): item["id"]
        for item in saved_plans
    }
    selected_label = st.selectbox(
        "Saved plans by date",
        options=list(plan_labels),
        help="Each generation is a new version. Older plans stay available.",
    )
    plan = load_plan(plan_labels[selected_label])

if plan:
    if plan.get("regime_name"):
        st.write(f"**Detected regime:** {plan['regime_name']}")
    if plan.get("strategy_name"):
        st.write(f"**Selected strategy:** {plan['strategy_name']}")
    else:
        st.write("**Selected strategy:** none (no available preferred long strategy)")
    st.write(f"**Why:** {plan.get('strategy_reason') or ''}")
    recommendation = plan.get("recommendation")
    if recommendation is not None:
        for note in recommendation.notes:
            st.info(note)
    st.caption(f"Candidate and holding prices are the {plan['price_source']}.")

    st.subheader("Proposed buys")
    if plan["buys"]:
        buys_table = pd.DataFrame(
            [
                {
                    "Ticker": row["Ticker"],
                    "Latest daily close": row["Price"],
                    "Close as of (UTC)": row["Price As Of"],
                    "Score": row["Score"],
                    "Suggested quantity": row["Quantity"],
                    "Dollar allocation": row["Allocation"],
                    "Reason": row["Reason"],
                }
                for row in plan["buys"]
            ]
        )
        st.dataframe(
            buys_table.style.format(
                {
                    "Latest daily close": "${:,.2f}",
                    "Score": "{:.4f}",
                    "Suggested quantity": "{:,.4f}",
                    "Dollar allocation": "${:,.2f}",
                }
            ),
            width="stretch",
            hide_index=True,
        )
    else:
        st.info(plan.get("no_purchase_explanation") or "No purchases qualify.")
    if plan.get("skipped_buys"):
        with st.expander("Candidates not allocated", expanded=False):
            st.dataframe(pd.DataFrame(plan["skipped_buys"]), width="stretch", hide_index=True)
    if plan.get("unavailable_universe"):
        st.caption(
            "Unavailable universe tickers (not treated as sells): "
            + ", ".join(plan["unavailable_universe"])
        )

    leftover_columns = st.columns(2)
    leftover_columns[0].metric("Cash after proposed buys", f"${plan['cash_after']:,.2f}")
    if plan["remaining_capacity_after"] is not None:
        leftover_columns[1].metric(
            "Capacity after proposed buys",
            f"${plan['remaining_capacity_after']:,.2f}",
        )
    else:
        leftover_columns[1].metric("Capacity after proposed buys", "n/a")

    st.subheader("Existing holdings")
    if not plan["holdings"]:
        st.caption("No open paper positions to review.")
    else:
        holdings_table = pd.DataFrame(
            [
                {
                    "ID": row.get("id"),
                    "Ticker": row.get("ticker"),
                    "Direction": row.get("direction"),
                    "Action": row.get("Action"),
                    "Signal": row.get("Signal"),
                    "Latest daily close": row.get("Price"),
                    "Close as of (UTC)": row.get("Price As Of"),
                    "Reason": row.get("Reason"),
                }
                for row in plan["holdings"]
            ]
        )
        st.dataframe(
            holdings_table.style.format({"Latest daily close": "${:,.2f}"}),
            width="stretch",
            hide_index=True,
        )
        st.caption(
            "KEEP means BUY or WAIT. REVIEW EXIT is an AVOID signal only. "
            "UNAVAILABLE never implies an exit. Proposed exits are not assumed done."
        )

    if plan.get("data_warnings"):
        with st.expander("Data warnings", expanded=False):
            for warning in plan["data_warnings"]:
                st.write(warning)
    st.download_button(
        "Download plan CSV",
        data=plan_to_csv(plan),
        file_name="todays_trading_plan.csv",
        mime="text/csv",
    )

    is_latest = bool(saved_plans) and plan["id"] == saved_plans[0]["id"]
    live_account = paper_storage.get_account_state()
    live_positions = paper_storage.get_open_positions()
    plan_is_stale, plan_stale_reason = detect_plan_staleness(
        plan, live_account, live_positions
    )
    actionable = []
    for row in plan["buys"]:
        if row.get("status") == "OPEN":
            actionable.append(
                (
                    row["recommendation_id"],
                    f"BUY {row['Ticker']} ${row['Allocation']:,.2f}",
                )
            )
    for row in plan["holdings"]:
        if row.get("status") == "OPEN" and row.get("Action") == REVIEW_EXIT:
            actionable.append(
                (
                    row["recommendation_id"],
                    f"REVIEW EXIT {row['ticker']} position #{row.get('id')}",
                )
            )
    if not is_latest:
        st.caption("This is an older plan. Record trades from the latest plan only.")
    elif plan_is_stale:
        st.warning(
            (plan_stale_reason or "This plan needs regeneration.")
            + " Generate a new plan before recording another trade. "
            "Saved recommendations are not trades."
        )
    elif not actionable:
        st.caption("No open BUY or REVIEW EXIT recommendations left on this plan.")
    else:
        action_labels = {label: recommendation_id for recommendation_id, label in actionable}
        with st.form("record_plan_trade"):
            chosen_action = st.selectbox(
                "Recommendation to record",
                options=list(action_labels),
            )
            record_submitted = st.form_submit_button("Record paper trade")
        if record_submitted:
            recommendation_id = action_labels[chosen_action]
            try:
                result = execute_recommendation(
                    recommendation_id,
                    lambda tickers: fetch_execution_snapshot(tickers, load_market_data),
                )
                st.success(
                    f"Recorded {result['trade_kind']} #{result['trade_id']} at "
                    f"${result['execution_price']:,.2f} ({SOURCE_LABEL}). "
                    "This plan now needs regeneration because the account snapshot changed."
                )
                st.rerun()
            except Exception as error:
                st.error(f"Could not record the paper trade: {error}")
        with st.form("dismiss_plan_recommendation"):
            dismiss_choice = st.selectbox(
                "Recommendation to dismiss",
                options=list(action_labels),
                key="dismiss_recommendation_choice",
            )
            dismiss_note = st.text_input("Optional dismissal note")
            dismiss_submitted = st.form_submit_button("Dismiss recommendation")
        if dismiss_submitted:
            try:
                dismiss_recommendation(action_labels[dismiss_choice], dismiss_note)
                st.success("Recommendation dismissed. It was not recorded as a trade.")
                st.rerun()
            except Exception as error:
                st.error(f"Could not dismiss the recommendation: {error}")


# ---------------------------------------------------------------------------
# Candidate Scan
# ---------------------------------------------------------------------------
st.header("Ranked LONG Candidates")
st.caption(
    f"Scans the configured universe ({len(STOCK_UNIVERSE)} tickers) using every "
    "registered strategy's own BUY signal and ranking - no strategy logic is "
    "changed here. Candidate Price is the scan-time daily close and is "
    f"informational only. Submitting an open fetches a new {SOURCE_LABEL} "
    "for the chosen ticker and every existing holding."
)

if st.button("Scan configured universe"):
    strategy_names = available_strategy_names()
    max_required_history = max(
        get_strategy(name).required_history_days for name in strategy_names
    )
    calendar_days = _calendar_days_for_trading_days(max_required_history)
    today = pd.Timestamp.today().normalize()
    start_date = today - pd.Timedelta(days=calendar_days)

    scan_tickers = list(dict.fromkeys(list(STOCK_UNIVERSE) + extra_strategy_benchmark_tickers()))
    with st.spinner(f"Loading price history for {len(scan_tickers)} tickers..."):
        try:
            price_data, cache_info = load_market_data(scan_tickers, start_date, today)
        except Exception as error:
            price_data, cache_info = {}, {}
            st.error(f"Could not load market data for the scan: {error}")

    candidates_by_strategy = scan_for_candidates(
        STOCK_UNIVERSE, strategy_names, price_data, get_strategy, min_price=MIN_STOCK_PRICE
    )
    st.session_state["candidate_scan"] = candidates_by_strategy
    st.session_state["candidate_scan_prices"] = latest_close_prices(price_data)

    failures = cache_info.get("Data Source Failures") if cache_info else None
    if failures:
        st.caption(f"{len(failures)} ticker(s) failed to load and were skipped from the scan.")

candidates_by_strategy = st.session_state.get("candidate_scan")
if not candidates_by_strategy:
    st.caption("Run a scan to see ranked LONG candidates from each strategy.")
else:
    for strategy_name, rows in candidates_by_strategy.items():
        st.subheader(f"{strategy_name} \u2014 {len(rows)} BUY candidate(s)")
        if not rows:
            st.caption("No BUY candidates from the latest scan.")
            continue

        candidates_table = pd.DataFrame(
            [
                {
                    "Ticker": row["Ticker"],
                    "Score": row["Score"],
                    "Scan daily close (informational)": row.get("Price"),
                    "Signal": row["Signal"],
                    "Reason": row["Reason"],
                }
                for row in rows
            ]
        )
        st.dataframe(
            candidates_table.style.format(
                {"Score": "{:.4f}", "Scan daily close (informational)": "${:,.2f}"}
            ),
            width="stretch",
            hide_index=True,
        )

        safe_key = strategy_name.replace(" ", "_")
        with st.form(f"open_position_form_{safe_key}"):
            ticker_options = [row["Ticker"] for row in rows]
            chosen_ticker = st.selectbox(
                "Ticker", options=ticker_options, key=f"ticker_select_{safe_key}"
            )
            allocation = st.number_input(
                "Allocation ($)",
                min_value=0.01,
                value=float(MAX_POSITION_VALUE),
                step=500.0,
                key=f"allocation_{safe_key}",
            )
            open_submitted = st.form_submit_button(f"Open Paper LONG Position ({strategy_name})")

        if open_submitted:
            chosen_result = next(row for row in rows if row["Ticker"] == chosen_ticker)
            holding_tickers = [position["ticker"] for position in paper_storage.get_open_positions()]
            required_tickers = list(dict.fromkeys([chosen_ticker, *holding_tickers]))
            try:
                execution_snapshot = fetch_execution_snapshot(
                    required_tickers, load_market_data
                )
                entry_quote = execution_snapshot["quotes"][chosen_ticker]
                position_id = paper_storage.open_position(
                    ticker=chosen_ticker,
                    entry_price=entry_quote["price"],
                    allocated_capital=allocation,
                    strategy=strategy_name,
                    reason=chosen_result.get("Reason", ""),
                    direction="LONG",
                    current_prices=execution_snapshot["prices"],
                )
                scan_price = chosen_result.get("Price")
                scan_note = ""
                if scan_price is not None and abs(float(scan_price) - entry_quote["price"]) > 1e-9:
                    scan_note = (
                        f" Scan-time informational close was ${float(scan_price):,.2f}."
                    )
                st.success(
                    f"Opened paper position #{position_id}: {chosen_ticker} LONG at "
                    f"${entry_quote['price']:,.2f} ({entry_quote['source_label']} "
                    f"as of {entry_quote['as_of']}; ${allocation:,.2f} allocated)."
                    + scan_note
                )
                st.rerun()
            except Exception as error:
                st.error(f"Could not open position: {error}")
