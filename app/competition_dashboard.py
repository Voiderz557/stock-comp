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
from paper_trading.portfolio import (
    latest_close_prices,
    scan_for_candidates,
    summarize_portfolio,
)
from strategies.registry import (
    available_strategy_names,
    extra_strategy_benchmark_tickers,
    get_strategy,
)


# ---------------------------------------------------------------------------
# Tunable, readable constants (not competition constraints - purely how much
# history this page fetches for its own display purposes).
# ---------------------------------------------------------------------------
CURRENT_PRICE_LOOKBACK_CALENDAR_DAYS = 10
TRADING_TO_CALENDAR_DAY_MULTIPLIER = 1.6
SCAN_HISTORY_BUFFER_CALENDAR_DAYS = 30


def _calendar_days_for_trading_days(trading_days):
    return int(trading_days * TRADING_TO_CALENDAR_DAY_MULTIPLIER) + SCAN_HISTORY_BUFFER_CALENDAR_DAYS


st.set_page_config(page_title="Paper Trading Competition Dashboard", layout="wide")
st.title("Paper Trading Competition Dashboard")
st.caption(
    "Every position on this page is simulated (paper trading only) and "
    "persisted to a local database. Nothing here places a real order."
)

paper_storage.initialize_storage()


# ---------------------------------------------------------------------------
# Portfolio Overview
# ---------------------------------------------------------------------------
st.header("Portfolio Overview")

account_state = paper_storage.get_account_state()
open_positions = paper_storage.get_open_positions()
closed_trades = paper_storage.get_closed_trades()

position_tickers = sorted({position["ticker"] for position in open_positions})
current_prices = {}
if position_tickers:
    today = pd.Timestamp.today().normalize()
    lookback_start = today - pd.Timedelta(days=CURRENT_PRICE_LOOKBACK_CALENDAR_DAYS)
    try:
        price_data, _cache_info = load_market_data(position_tickers, lookback_start, today)
        current_prices = latest_close_prices(price_data)
    except Exception as error:
        st.warning(f"Could not refresh live prices for open positions: {error}")

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
        f"Current short exposure ${summary['Current Short Exposure']:,.2f}. "
        "Market moves that push gross exposure above starting capital block "
        "new opens but never force a close."
    )
else:
    capacity_columns[1].metric("Current Long Exposure", "n/a")
    capacity_columns[2].metric("Gross Exposure", "n/a")
    capacity_columns[3].metric("Remaining Capacity", "n/a")
    if summary["Capacity Error"]:
        st.warning(summary["Capacity Error"])

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
        exit_price = current_prices.get(position["ticker"])
        if exit_price is None:
            st.error(
                f"No current price available for {position['ticker']}; cannot close "
                "this position right now."
            )
        else:
            try:
                realized_pnl = paper_storage.close_position(position_id, exit_price)
                st.success(
                    f"Closed #{position_id} {position['ticker']} at ${exit_price:,.2f} "
                    f"(realized P&L ${realized_pnl:+,.2f})."
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
# Candidate Scan
# ---------------------------------------------------------------------------
st.header("Ranked LONG Candidates")
st.caption(
    f"Scans the configured universe ({len(STOCK_UNIVERSE)} tickers) using every "
    "registered strategy's own BUY signal and ranking - no strategy logic is "
    "changed here."
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
                    "Price": row.get("Price"),
                    "Signal": row["Signal"],
                    "Reason": row["Reason"],
                }
                for row in rows
            ]
        )
        st.dataframe(
            candidates_table.style.format({"Score": "{:.4f}", "Price": "${:,.2f}"}),
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
            entry_price = chosen_result.get("Price")
            if entry_price is None:
                st.error(f"No price available for {chosen_ticker}; cannot open a position.")
            else:
                try:
                    position_id = paper_storage.open_position(
                        ticker=chosen_ticker,
                        entry_price=entry_price,
                        allocated_capital=allocation,
                        strategy=strategy_name,
                        reason=chosen_result.get("Reason", ""),
                        direction="LONG",
                        current_prices=current_prices,
                    )
                    st.success(
                        f"Opened paper position #{position_id}: {chosen_ticker} LONG at "
                        f"${entry_price:,.2f} (${allocation:,.2f} allocated)."
                    )
                    st.rerun()
                except Exception as error:
                    st.error(f"Could not open position: {error}")
