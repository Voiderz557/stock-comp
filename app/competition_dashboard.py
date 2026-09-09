"""PAPER-TRADING / COMPETITION recommendation dashboard.

Answers "what should I invest in right now?" by scanning the configured
stock universe with Baseline, Momentum V2, and Aggressive Momentum V1, then
showing the strongest LONG and SHORT candidates and a recommendation-only
paper allocation.

SAFETY:
- This dashboard is RECOMMENDATION-ONLY. It never sends an order to any
  broker and never touches real money.
- No margin or leverage is modeled beyond the configured exposure caps.
- Short-sale proceeds are never assumed spendable (see
  paper_trading/storage.py for the exact cash-accounting rules).
- LONG and SHORT share ONE combined gross-exposure capacity (starting
  capital) - shorts are NOT a separate extra wallet stacked on top of long
  buying power. Opening $40,000 of shorts on a $100,000 account leaves only
  $60,000 of capacity for longs, not $100,000.
- Historical short-selling backtesting is NOT implemented here - this
  dashboard is a live/current scanner, separate from the existing long-only
  historical backtester in backtesting/engine.py, which is unmodified.

PERSISTENCE:
Unlike a plain Streamlit app, the paper portfolio (open positions, cash,
realized P&L, closed-trade history) is stored in a SQLite file via
`paper_trading.storage.PaperPortfolioStore`, so closing and reopening this
app does NOT reset it. Only `st.session_state` (the market scan results and
this run's widget values) resets on restart - the account state does not.
"""

from datetime import datetime, timezone

import pandas as pd
import streamlit as st

from app.competition_recommender import (
    build_paper_portfolio,
    build_recommendation_row,
)
from backtesting.engine import get_ticker_data
from config import (
    ALLOW_SHORTS_DEFAULT,
    MAX_POSITION_VALUE,
    MAX_SHORT_POSITION_VALUE,
    MIN_STOCK_PRICE,
    PAPER_PORTFOLIO_DB_PATH,
    PAPER_PORTFOLIO_STARTING_CASH,
    STOCK_UNIVERSE,
)
from data.market_data import load_market_data
from paper_trading.storage import LONG, PaperPortfolioStore, SHORT
from strategies.registry import available_strategy_names, get_strategy, invoke_analyze


st.set_page_config(page_title="Competition Paper-Trading Dashboard", layout="wide")
st.title("Competition Paper-Trading Dashboard")
st.warning(
    "PAPER / VIRTUAL COMPETITION USE ONLY. This dashboard never sends real "
    "orders to a broker; every position below is a local, recommendation-only "
    "bookkeeping entry."
)


@st.cache_resource
def get_store():
    """One persistent SQLite-backed store per process, reused across reruns.

    Streamlit reruns this script on every interaction, but `st.cache_resource`
    means the same PaperPortfolioStore (and therefore the same open SQLite
    connection) is reused instead of being recreated - and because the data
    lives in the SQLite file, not in this object, it also survives the whole
    app process restarting.
    """
    return PaperPortfolioStore(
        PAPER_PORTFOLIO_DB_PATH, starting_cash=PAPER_PORTFOLIO_STARTING_CASH
    )


store = get_store()

for key, default in {
    "scan_rows": None,
    "scan_prices": {},
}.items():
    if key not in st.session_state:
        st.session_state[key] = default


# ---------------------------------------------------------------------------
# Step 1: load the existing (persisted) portfolio state and show it using
# current prices, before anything else on the page.
# ---------------------------------------------------------------------------
st.header("Persisted Paper Portfolio")

open_positions = store.list_open_positions()
position_tickers = sorted({position.ticker for position in open_positions})

current_prices_for_positions = {}
if position_tickers:
    with st.spinner("Fetching current prices for open positions..."):
        position_data, _ = load_market_data(
            position_tickers,
            pd.Timestamp.today().normalize() - pd.Timedelta(days=10),
            pd.Timestamp.today().normalize(),
        )
        for ticker in position_tickers:
            ticker_frame = get_ticker_data(position_data, ticker)
            if ticker_frame is not None and not ticker_frame["Close"].dropna().empty:
                current_prices_for_positions[ticker] = float(
                    ticker_frame["Close"].dropna().iloc[-1]
                )

valuation = store.valuation_summary(current_prices_for_positions)

summary_cols = st.columns(4)
summary_cols[0].metric("Cash", f"${valuation['Cash']:,.2f}")
summary_cols[1].metric("Realized P&L (all-time)", f"${valuation['Realized P&L']:,.2f}")
summary_cols[2].metric(
    "Unrealized P&L (open)", f"${valuation['Total Unrealized P&L']:,.2f}"
)
summary_cols[3].metric(
    "Total Portfolio Value", f"${valuation['Total Portfolio Value']:,.2f}"
)

exposure_summary_cols = st.columns(5)
exposure_summary_cols[0].metric(
    "Current Long Exposure", f"${valuation['Current Long Exposure']:,.2f}"
)
exposure_summary_cols[1].metric(
    "Current Short Exposure", f"${valuation['Current Short Exposure']:,.2f}",
    help="abs(quantity * CURRENT price), not entry price - this rises and "
    "falls with the market price.",
)
exposure_summary_cols[2].metric(
    "Gross Exposure", f"${valuation['Gross Exposure']:,.2f}",
    help="Current Long Exposure + Current Short Exposure. LONG and SHORT "
    "share one combined limit - this can never exceed Starting Capital.",
)
exposure_summary_cols[3].metric("Net Exposure", f"${valuation['Net Exposure']:,.2f}")
exposure_summary_cols[4].metric(
    "Remaining Capacity",
    f"${valuation['Remaining Capacity']:,.2f}",
    help=f"Starting Capital (${valuation['Starting Capital']:,.2f}) minus "
    "Gross Exposure - the shared room left for a NEW long OR short.",
)

if not open_positions:
    st.info("No open paper positions yet. Recommendations appear below.")
else:
    rows = []
    for item in valuation["Positions"]:
        position = item["Position"]
        rows.append(
            {
                "ID": position.id,
                "Ticker": position.ticker,
                "Direction": position.direction,
                "Entry Price": position.entry_price,
                "Quantity": round(position.quantity, 4),
                "Allocated Capital": position.allocated_capital,
                "Current Price": item["Current Price"],
                "Market Value": item["Market Value"],
                "Initial Short Exposure": item["Initial Short Exposure"],
                "Current Short Exposure": item["Current Short Exposure"],
                "Unrealized P&L": item["Unrealized P&L"],
                "Entry Time (UTC)": position.entry_timestamp,
                "Strategy": position.strategy,
            }
        )
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)

    st.caption("Close a position at its current price:")
    close_columns = st.columns(min(4, len(open_positions)) or 1)
    for index, position in enumerate(open_positions):
        exit_price = current_prices_for_positions.get(position.ticker)
        button_label = f"Close {position.ticker} ({position.direction})"
        with close_columns[index % len(close_columns)]:
            disabled = exit_price is None
            if st.button(button_label, key=f"close_{position.id}", disabled=disabled):
                store.close_position(position.id, exit_price)
                st.success(f"Closed {position.ticker} at ${exit_price:,.2f}.")
                st.rerun()
            if disabled:
                st.caption("No current price available yet.")

closed_trades = store.list_closed_trades()
with st.expander(f"Closed trade history ({len(closed_trades)})", expanded=False):
    if not closed_trades:
        st.info("No closed trades yet.")
    else:
        st.dataframe(
            pd.DataFrame([trade.__dict__ for trade in closed_trades]),
            width="stretch",
            hide_index=True,
        )

st.divider()

# ---------------------------------------------------------------------------
# Step 2: scan the universe and build LONG/SHORT recommendations.
# ---------------------------------------------------------------------------
st.header("Scan Universe")

strategy_names = available_strategy_names()
selected_strategies = st.multiselect(
    "Strategies to run",
    options=strategy_names,
    default=strategy_names,
    help="SHORT recommendations require Aggressive Momentum V1 to be selected.",
)

settings_col_1, settings_col_2, settings_col_3 = st.columns(3)
with settings_col_1:
    dashboard_starting_capital = st.number_input(
        "Starting capital for NEW recommendations ($)",
        min_value=0.01,
        value=float(PAPER_PORTFOLIO_STARTING_CASH),
        step=10_000.0,
        help=(
            "ONE shared LONG+SHORT capacity pool for the recommendation "
            "below - not a separate long budget. The persisted paper "
            "account's actual capital (above) is unaffected until you "
            "click 'Open' on a recommendation."
        ),
    )
    min_stock_price = st.number_input(
        "Minimum eligible stock price ($)",
        min_value=0.0,
        value=float(MIN_STOCK_PRICE),
        step=1.0,
    )
with settings_col_2:
    max_long_position_value = st.number_input(
        "Maximum LONG allocation per stock ($)",
        min_value=0.01,
        value=float(MAX_POSITION_VALUE),
        step=1_000.0,
    )
    allow_shorts = st.checkbox("Allow Shorts", value=ALLOW_SHORTS_DEFAULT)
with settings_col_3:
    max_short_position_value = st.number_input(
        "Max SHORT exposure per stock ($) - PLACEHOLDER",
        min_value=0.01,
        value=float(MAX_SHORT_POSITION_VALUE),
        step=1_000.0,
        help="Placeholder until official competition short-selling rules are confirmed.",
    )

st.caption(
    f"Universe: {len(STOCK_UNIVERSE)} tickers from the configured scanner "
    "universe (data/scanner_universe.py). No tickers need to be typed in "
    "manually."
)

if st.button("Scan Universe Now", type="primary"):
    if not selected_strategies:
        st.error("Select at least one strategy.")
    else:
        selected_definitions = [get_strategy(name) for name in selected_strategies]
        # Same calendar-buffer heuristic used by the historical backtester
        # (backtesting/engine.py::download_backtest_data) to turn a required
        # number of trading rows into enough calendar days of history.
        required_history_days = max(
            definition.required_history_days for definition in selected_definitions
        )
        end_date = pd.Timestamp.today().normalize()
        start_date = end_date - pd.Timedelta(days=required_history_days * 3)
        scan_tickers = list(STOCK_UNIVERSE)
        for definition in selected_definitions:
            if (
                definition.benchmark_ticker
                and definition.benchmark_ticker not in scan_tickers
            ):
                scan_tickers.append(definition.benchmark_ticker)

        with st.spinner(f"Downloading data for {len(scan_tickers)} tickers..."):
            downloaded_data, cache_report = load_market_data(
                scan_tickers, start_date, end_date
            )

        rows = []
        current_prices = {}
        for ticker in STOCK_UNIVERSE:
            ticker_data = get_ticker_data(downloaded_data, ticker)
            if ticker_data is None or ticker_data["Close"].dropna().empty:
                continue

            current_price = float(ticker_data["Close"].dropna().iloc[-1])
            current_prices[ticker] = current_price

            results_by_strategy = {}
            for name in selected_strategies:
                definition = get_strategy(name)
                benchmark_frame = None
                if definition.benchmark_ticker:
                    benchmark_frame = get_ticker_data(
                        downloaded_data, definition.benchmark_ticker
                    )
                results_by_strategy[name] = invoke_analyze(
                    definition.analyze,
                    ticker,
                    ticker_data,
                    benchmark_data=benchmark_frame,
                )
            rows.append(
                build_recommendation_row(
                    ticker,
                    current_price,
                    results_by_strategy.get("Baseline"),
                    results_by_strategy.get("Momentum V2"),
                    results_by_strategy.get("Aggressive Momentum V1"),
                )
            )

        st.session_state.scan_rows = rows
        st.session_state.scan_prices = current_prices
        st.session_state.scan_cache_report = cache_report
        st.success(
            f"Scanned {len(rows)} of {len(STOCK_UNIVERSE)} tickers "
            f"({cache_report['Status']})."
        )

rows = st.session_state.scan_rows

if rows:
    table = pd.DataFrame(rows).sort_values(
        "Combined Long Score", ascending=False
    )
    st.subheader("Combined Table")
    st.dataframe(table, width="stretch", hide_index=True)

    long_candidates_col, short_candidates_col = st.columns(2)
    with long_candidates_col:
        st.subheader("Strongest LONG Candidates")
        long_view = table[table["Recommended Action"] == LONG].sort_values(
            "Combined Long Score", ascending=False
        )
        if long_view.empty:
            st.info("No LONG candidates right now.")
        else:
            st.dataframe(
                long_view[["Ticker", "Current Price", "Combined Long Score", "Reason"]],
                width="stretch",
                hide_index=True,
            )
    with short_candidates_col:
        st.subheader("Strongest SHORT Candidates")
        short_view = table[table["Recommended Action"] == SHORT].sort_values(
            "Combined Short Score", ascending=False
        )
        if short_view.empty:
            st.info("No SHORT candidates right now.")
        else:
            st.dataframe(
                short_view[["Ticker", "Current Price", "Combined Short Score", "Reason"]],
                width="stretch",
                hide_index=True,
            )

    st.divider()
    st.header("Recommended Paper Portfolio")
    st.caption(
        "Recommendation only. Nothing here is persisted until you click "
        "'Open' below. LONG and SHORT share ONE capacity pool (Starting "
        "Capital) - shorts are NOT a separate extra wallet added on top of "
        "long buying power."
    )

    portfolio = build_paper_portfolio(
        rows,
        starting_capital=dashboard_starting_capital,
        allow_shorts=allow_shorts,
        max_long_position_value=max_long_position_value,
        max_short_position_value=max_short_position_value,
        min_stock_price=min_stock_price,
    )

    exposure_cols = st.columns(4)
    exposure_cols[0].metric("Gross Long Exposure", f"${portfolio['Gross Long Exposure']:,.2f}")
    exposure_cols[1].metric("Gross Short Exposure", f"${portfolio['Gross Short Exposure']:,.2f}")
    exposure_cols[2].metric(
        "Gross Exposure", f"${portfolio['Gross Exposure']:,.2f}",
        help=f"Must never exceed Starting Capital (${dashboard_starting_capital:,.2f}).",
    )
    exposure_cols[3].metric("Net Exposure", f"${portfolio['Net Exposure']:,.2f}")

    long_column, short_column = st.columns(2)
    with long_column:
        st.subheader("LONGS")
        if not portfolio["Longs"]:
            st.info("No recommended LONG allocations.")
        else:
            for index, item in enumerate(portfolio["Longs"]):
                cols = st.columns([2, 2, 2, 4, 2])
                cols[0].write(item["Ticker"])
                cols[1].write(f"${item['Allocation']:,.2f}")
                cols[2].write(f"{item['Score']:.4f}")
                cols[3].write(item["Reason"])
                current_price = current_prices_for_positions.get(
                    item["Ticker"], st.session_state.scan_prices.get(item["Ticker"])
                )
                if cols[4].button(
                    "Open", key=f"open_long_{item['Ticker']}_{index}"
                ):
                    if current_price is None:
                        st.error(f"No current price available for {item['Ticker']}.")
                    else:
                        try:
                            store.open_position(
                                ticker=item["Ticker"],
                                direction=LONG,
                                entry_price=current_price,
                                allocated_capital=item["Allocation"],
                                strategy="Combined (paper dashboard)",
                                reason=item["Reason"],
                                current_prices=current_prices_for_positions,
                            )
                        except ValueError as error:
                            st.error(str(error))
                        else:
                            st.success(f"Opened LONG {item['Ticker']}.")
                            st.rerun()

    with short_column:
        st.subheader("SHORTS")
        if not allow_shorts:
            st.info("Shorting is disabled ('Allow Shorts' is off).")
        elif not portfolio["Shorts"]:
            st.info("No sufficiently bearish SHORT candidates right now.")
        else:
            for index, item in enumerate(portfolio["Shorts"]):
                cols = st.columns([2, 2, 2, 4, 2])
                cols[0].write(item["Ticker"])
                cols[1].write(f"${item['Exposure']:,.2f}")
                cols[2].write(f"{item['Score']:.4f}")
                cols[3].write(item["Reason"])
                current_price = st.session_state.scan_prices.get(item["Ticker"])
                if cols[4].button(
                    "Open", key=f"open_short_{item['Ticker']}_{index}"
                ):
                    if current_price is None:
                        st.error(f"No current price available for {item['Ticker']}.")
                    else:
                        try:
                            store.open_position(
                                ticker=item["Ticker"],
                                direction=SHORT,
                                entry_price=current_price,
                                allocated_capital=item["Exposure"],
                                strategy="Aggressive Momentum V1 (paper dashboard)",
                                reason=item["Reason"],
                                current_prices=current_prices_for_positions,
                            )
                        except ValueError as error:
                            st.error(str(error))
                        else:
                            st.success(f"Opened SHORT {item['Ticker']}.")
                            st.rerun()

    st.subheader("REMAINING CAPACITY")
    st.metric(
        "Recommended-allocation capacity remaining",
        f"${portfolio['Remaining Capacity']:,.2f}",
        help="Shared LONG+SHORT capacity left unused by this recommendation.",
    )
else:
    st.info("Click 'Scan Universe Now' to generate recommendations.")
