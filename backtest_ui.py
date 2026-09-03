import pandas as pd
import streamlit as st

from backtest import run_backtest
from config import (
    BACKTEST_BENCHMARK,
    BACKTEST_END_DATE,
    BACKTEST_FEE_RATE,
    BACKTEST_MAX_POSITIONS,
    BACKTEST_START_DATE,
    BACKTEST_STARTING_CASH,
)
from market_data import clear_market_data_cache


st.set_page_config(
    page_title="Stock Strategy Backtester",
    layout="wide",
)

st.title("Stock Strategy Backtester")
st.write(
    "Test the current 3-point strategy using weekly, equal-weight "
    "portfolio rebalancing."
)

if "confirm_clear_cache" not in st.session_state:
    st.session_state.confirm_clear_cache = False

if st.session_state.confirm_clear_cache:
    st.warning("Delete all locally cached market data? This cannot be undone.")
    confirm_column, cancel_column, _ = st.columns([1, 1, 3])
    if confirm_column.button("Confirm clear", type="primary"):
        clear_market_data_cache()
        st.session_state.confirm_clear_cache = False
        st.success("Market data cache cleared.")
    if cancel_column.button("Cancel"):
        st.session_state.confirm_clear_cache = False
        st.rerun()
elif st.button("Clear market data cache"):
    st.session_state.confirm_clear_cache = True
    st.rerun()

with st.form("backtest_settings"):
    date_column_1, date_column_2 = st.columns(2)

    with date_column_1:
        start_date = st.date_input(
            "Start date",
            value=pd.Timestamp(BACKTEST_START_DATE).date(),
        )

    with date_column_2:
        end_date = st.date_input(
            "End date",
            value=pd.Timestamp(BACKTEST_END_DATE).date(),
        )

    input_column_1, input_column_2, input_column_3, input_column_4 = st.columns(4)

    with input_column_1:
        starting_cash = st.number_input(
            "Starting cash ($)",
            min_value=0.0,
            value=float(BACKTEST_STARTING_CASH),
            step=10_000.0,
        )

    with input_column_2:
        max_positions = st.number_input(
            "Maximum positions",
            min_value=1,
            value=int(BACKTEST_MAX_POSITIONS),
            step=1,
        )

    with input_column_3:
        benchmark = st.text_input(
            "Benchmark ticker",
            value=BACKTEST_BENCHMARK,
        )

    with input_column_4:
        fee_percent = st.number_input(
            "Transaction fee / slippage (%)",
            min_value=0.0,
            value=float(BACKTEST_FEE_RATE * 100),
            step=0.01,
            format="%.3f",
        )

    run_button = st.form_submit_button(
        "Run Backtest",
        type="primary",
    )

if "backtest_results" not in st.session_state:
    st.session_state.backtest_results = None

if run_button:
    validation_errors = []

    if start_date >= end_date:
        validation_errors.append("Start date must be before end date.")
    if starting_cash <= 0:
        validation_errors.append("Starting cash must be positive.")
    if max_positions < 1:
        validation_errors.append("Maximum positions must be at least 1.")
    if not benchmark.strip():
        validation_errors.append("Benchmark ticker cannot be empty.")

    if validation_errors:
        for message in validation_errors:
            st.error(message)
    else:
        cache_status = st.empty()

        def show_cache_status(status, ticker):
            cache_status.info(f"{status}: {ticker}")

        with st.spinner("Downloading data and running the backtest..."):
            try:
                st.session_state.backtest_results = run_backtest(
                    start_date=start_date,
                    end_date=end_date,
                    starting_cash=starting_cash,
                    max_positions=int(max_positions),
                    benchmark=benchmark,
                    fee_rate=fee_percent / 100,
                    status_callback=show_cache_status,
                )
                final_cache_status = st.session_state.backtest_results[
                    "Market Data Cache"
                ]["Status"]
                cache_status.success(f"Market data: {final_cache_status}")
            except (ValueError, RuntimeError) as error:
                st.session_state.backtest_results = None
                st.error(str(error))
            except Exception as error:
                st.session_state.backtest_results = None
                st.error(f"The backtest could not be completed: {error}")

results = st.session_state.backtest_results

if results is not None:
    st.subheader("Results")

    cache_report = results["Market Data Cache"]
    st.info(
        f"Market data: {cache_report['Status']}  |  "
        f"Cache: {cache_report['Cache Directory']}"
    )
    if results["Universe Approximate"]:
        snapshot_dates = ", ".join(
            str(date.date()) for date in results["Universe Snapshot Dates"]
        )
        st.warning(
            "Historical Nasdaq-100 universe is approximate. "
            f"Snapshots used: {snapshot_dates}. "
            f"{results['Universe Source']}"
        )

    difference = results["Total Return"] - results["Benchmark Return"]
    metric_row_1 = st.columns(3)
    metric_row_2 = st.columns(3)

    metric_row_1[0].metric(
        "Starting Value",
        f"${results['Starting Value']:,.2f}",
    )
    metric_row_1[1].metric(
        "Ending Value",
        f"${results['Ending Value']:,.2f}",
    )
    metric_row_1[2].metric(
        "Total Return",
        f"{results['Total Return']:+.2%}",
    )
    metric_row_2[0].metric(
        f"{results['Benchmark']} Return",
        f"{results['Benchmark Return']:+.2%}",
    )
    metric_row_2[1].metric(
        "Difference vs Benchmark",
        f"{difference:+.2%}",
    )
    metric_row_2[2].metric(
        "Number of Trades",
        f"{len(results['Trades']):,}",
    )

    st.subheader("Portfolio vs Benchmark")
    history_table = pd.DataFrame(results["Portfolio History"])
    history_table = history_table.set_index("Date")
    st.line_chart(
        history_table[["Portfolio Value", "Benchmark Value"]],
        y_label="Value ($)",
    )

    st.subheader("Trade Log")
    trade_table = pd.DataFrame(results["Trades"])

    if trade_table.empty:
        st.info("The strategy did not make any trades in this period.")
    else:
        trade_table["Date"] = pd.to_datetime(trade_table["Date"]).dt.date
        trade_table["Shares"] = trade_table["Shares"].round(4)
        trade_table["Price"] = trade_table["Price"].map(
            lambda price: f"${price:,.2f}"
        )
        trade_table["Fee"] = trade_table["Fee"].map(
            lambda fee: f"${fee:,.2f}"
        )
        st.dataframe(trade_table, width="stretch", hide_index=True)

    st.subheader("Final Holdings")
    holdings_table = pd.DataFrame(results["Final Holdings"])

    if holdings_table.empty:
        st.info("The strategy finished the period holding only cash.")
    else:
        holdings_table["Shares"] = holdings_table["Shares"].round(4)
        holdings_table["Final Price"] = holdings_table["Final Price"].map(
            lambda price: f"${price:,.2f}"
        )
        holdings_table["Market Value"] = holdings_table["Market Value"].map(
            lambda value: f"${value:,.2f}"
        )
        st.dataframe(holdings_table, width="stretch", hide_index=True)

    st.warning(
        "This test uses today's stock universe for historical periods, so it "
        "still contains survivorship bias. Treat the result as preliminary."
    )
