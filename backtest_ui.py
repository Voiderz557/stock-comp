import pandas as pd
import streamlit as st

from backtest import run_backtest
from backtest_periods import DURATION_OPTIONS, generate_random_periods
from config import (
    BACKTEST_BENCHMARK,
    BACKTEST_FEE_RATE,
    BACKTEST_STARTING_CASH,
    MAX_POSITION_VALUE,
    MIN_STOCK_PRICE,
)
from historical_universe import SNAPSHOT_DATES
from market_data import clear_market_data_cache


st.set_page_config(page_title="Stock Strategy Backtester", layout="wide")

st.title("Stock Strategy Backtester")
st.write(
    "Test the same strategy across reproducible random market periods. "
    "Choose the allowed date range; the app chooses the actual test windows."
)

first_supported_date = SNAPSHOT_DATES[0].date()
today = pd.Timestamp.today().normalize().date()
default_earliest = max(pd.Timestamp("2022-01-01").date(), first_supported_date)
default_latest = min(pd.Timestamp("2025-12-31").date(), today)

with st.form("backtest_settings"):
    primary_1, primary_2, primary_3 = st.columns(3)
    with primary_1:
        duration = st.selectbox(
            "Test Duration",
            options=list(DURATION_OPTIONS),
            index=list(DURATION_OPTIONS).index("5 months"),
            help="Length of every randomly selected test window.",
        )
    with primary_2:
        earliest_allowed = st.date_input(
            "Earliest Allowed Date",
            value=default_earliest,
            min_value=first_supported_date,
            max_value=today,
            help="Boundary only: no generated test may start before this date.",
        )
    with primary_3:
        latest_allowed = st.date_input(
            "Latest Allowed Date",
            value=default_latest,
            min_value=first_supported_date,
            max_value=today,
            help="Boundary only: every generated test must end by this date.",
        )

    primary_4, primary_5 = st.columns(2)
    with primary_4:
        number_of_tests = st.selectbox(
            "Number of Random Tests",
            options=[1, 2, 5, 10, 20],
            index=2,
            help="More tests sample more periods but take longer to run.",
        )
    with primary_5:
        random_seed = st.number_input(
            "Random Seed",
            value=42,
            step=1,
            help="Use the same seed and settings to reproduce the same periods.",
        )

    with st.expander("Advanced Settings", expanded=False):
        advanced_1, advanced_2 = st.columns(2)
        with advanced_1:
            starting_cash = st.number_input(
                "Starting cash ($)",
                min_value=0.01,
                value=float(BACKTEST_STARTING_CASH),
                step=10_000.0,
            )
            max_position_value = st.number_input(
                "Maximum initial allocation per stock ($)",
                min_value=0.01,
                value=float(MAX_POSITION_VALUE),
                step=1_000.0,
            )
            min_stock_price = st.number_input(
                "Minimum eligible stock price ($)",
                min_value=0.0,
                value=float(MIN_STOCK_PRICE),
                step=1.0,
            )
        with advanced_2:
            benchmark = st.text_input("Benchmark", value=BACKTEST_BENCHMARK)
            fee_percent = st.number_input(
                "Transaction fee / slippage (%)",
                min_value=0.0,
                value=float(BACKTEST_FEE_RATE * 100),
                step=0.01,
                format="%.3f",
            )

    run_button = st.form_submit_button(
        "Run Tests", type="primary", use_container_width=True
    )

with st.expander("Data", expanded=False):
    st.caption("Cached prices make repeated backtests faster.")
    if "confirm_clear_cache" not in st.session_state:
        st.session_state.confirm_clear_cache = False
    if st.session_state.confirm_clear_cache:
        st.warning("Delete all locally cached market data? This cannot be undone.")
        confirm_column, cancel_column = st.columns(2)
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

if "backtest_results" not in st.session_state:
    st.session_state.backtest_results = []
if "backtest_failures" not in st.session_state:
    st.session_state.backtest_failures = []

if run_button:
    try:
        periods = generate_random_periods(
            duration,
            earliest_allowed,
            latest_allowed,
            int(number_of_tests),
            int(random_seed),
        )
        if not benchmark.strip():
            raise ValueError("Benchmark ticker cannot be empty.")

        progress = st.progress(0, text="Preparing tests...")
        phase_status = st.empty()
        cache_status = st.empty()
        completed_results = []
        failed_tests = []
        for test_number, (period_start, period_end) in enumerate(periods, start=1):
            def show_cache_status(status, ticker, current=test_number):
                cache_status.info(
                    f"Test {current}/{number_of_tests} - {status}: {ticker}"
                )

            def show_phase(phase, current=test_number):
                phase_status.info(f"Test {current}/{number_of_tests}\n\n{phase}")
                progress.progress(
                    (current - 1) / number_of_tests,
                    text=f"Test {current}/{number_of_tests} - {phase}",
                )

            try:
                show_phase("Loading market data...")
                result = run_backtest(
                    start_date=period_start,
                    end_date=period_end,
                    starting_cash=starting_cash,
                    benchmark=benchmark,
                    fee_rate=fee_percent / 100,
                    min_stock_price=min_stock_price,
                    max_position_value=max_position_value,
                    status_callback=show_cache_status,
                    phase_callback=show_phase,
                )
                result["Test"] = test_number
                completed_results.append(result)
                phase_status.success(f"Test {test_number}/{number_of_tests}\n\nComplete")
                progress.progress(
                    test_number / number_of_tests,
                    text=f"Test {test_number}/{number_of_tests} - Complete",
                )
            except Exception as error:
                failed_tests.append(
                    {
                        "Test": test_number,
                        "Requested Start": period_start.date(),
                        "Requested End": period_end.date(),
                        "Error": str(error),
                    }
                )
                phase_status.error(
                    f"Test {test_number}/{number_of_tests}\n\nFailed - continuing"
                )
                progress.progress(
                    test_number / number_of_tests,
                    text=f"Test {test_number}/{number_of_tests} - Failed; continuing...",
                )

        st.session_state.backtest_results = completed_results
        st.session_state.backtest_failures = failed_tests
        if completed_results:
            cache_status.success("Finished all requested tests.")
        else:
            cache_status.error("No tests completed successfully.")
    except (ValueError, RuntimeError) as error:
        st.session_state.backtest_results = []
        st.session_state.backtest_failures = []
        st.error(str(error))
    except Exception as error:
        st.session_state.backtest_results = []
        st.session_state.backtest_failures = []
        st.error(f"The tests could not be completed: {error}")

results = st.session_state.backtest_results
failed_tests = st.session_state.backtest_failures

if failed_tests:
    st.warning(
        f"{len(failed_tests)} test(s) could not run. Other tests continued normally."
    )
    st.dataframe(pd.DataFrame(failed_tests), width="stretch", hide_index=True)

if results:
    st.subheader("Test Summary")
    unavailable_constituents = sorted(
        {
            ticker
            for result in results
            for ticker in result["Market Data Cache"].get(
                "Unavailable Valid Constituents", []
            )
        }
    )
    if unavailable_constituents:
        st.warning(
            "Historical data unavailable for "
            f"{len(unavailable_constituents)} valid constituents: "
            f"{', '.join(unavailable_constituents)}"
        )
        failure_rows = [
            failure
            for result in results
            for failure in result["Market Data Cache"].get(
                "Data Source Failures", []
            )
        ]
        with st.expander("Historical data failure details", expanded=False):
            st.dataframe(
                pd.DataFrame(failure_rows).drop_duplicates(),
                width="stretch",
                hide_index=True,
            )
    benchmark_name = results[0]["Benchmark"]
    benchmark_return_label = (
        "SPY Return" if benchmark_name == "SPY" else f"{benchmark_name} Return"
    )
    summary_rows = []
    for result in results:
        excess_return = result["Total Return"] - result["Benchmark Return"]
        summary_rows.append(
            {
                "Test": result["Test"],
                "Start": result["Start Date"].date(),
                "End": result["End Date"].date(),
                "Strategy Return": result["Total Return"],
                benchmark_return_label: result["Benchmark Return"],
                "Excess Return": excess_return,
                "Number of Trades": len(result["Trades"]),
            }
        )
    summary = pd.DataFrame(summary_rows)
    st.dataframe(
        summary.style.format(
            {
                "Strategy Return": "{:+.2%}",
                benchmark_return_label: "{:+.2%}",
                "Excess Return": "{:+.2%}",
            }
        ),
        width="stretch",
        hide_index=True,
    )

    average_strategy = summary["Strategy Return"].mean()
    median_strategy = summary["Strategy Return"].median()
    average_benchmark = summary[benchmark_return_label].mean()
    average_excess = summary["Excess Return"].mean()
    win_rate = (summary["Excess Return"] > 0).mean()
    best_row = summary.loc[summary["Strategy Return"].idxmax()]
    worst_row = summary.loc[summary["Strategy Return"].idxmin()]

    metric_row_1 = st.columns(4)
    metric_row_1[0].metric("Average Strategy Return", f"{average_strategy:+.2%}")
    metric_row_1[1].metric("Median Strategy Return", f"{median_strategy:+.2%}")
    metric_row_1[2].metric(
        f"Average {benchmark_name} Return", f"{average_benchmark:+.2%}"
    )
    metric_row_1[3].metric("Average Excess Return", f"{average_excess:+.2%}")
    metric_row_2 = st.columns(3)
    comparison_label = (
        "Win Rate vs SPY" if benchmark_name == "SPY" else f"Win Rate vs {benchmark_name}"
    )
    metric_row_2[0].metric(comparison_label, f"{win_rate:.0%}")
    metric_row_2[1].metric(
        "Best Test",
        f"Test {int(best_row['Test'])}: {best_row['Strategy Return']:+.2%}",
    )
    metric_row_2[2].metric(
        "Worst Test",
        f"Test {int(worst_row['Test'])}: {worst_row['Strategy Return']:+.2%}",
    )

    selected_test = st.selectbox(
        "Inspect Test",
        options=[result["Test"] for result in results],
        format_func=lambda test: f"Test {test}",
    )
    result = next(item for item in results if item["Test"] == selected_test)

    if result["Universe Approximate"]:
        snapshot_dates = ", ".join(
            str(date.date()) for date in result["Universe Snapshot Dates"]
        )
        st.warning(
            "Historical Nasdaq-100 membership is approximate. "
            f"Snapshots used: {snapshot_dates}. {result['Universe Source']}"
        )

    st.subheader("Portfolio vs Benchmark")
    history_table = pd.DataFrame(result["Portfolio History"]).set_index("Date")
    st.line_chart(
        history_table[["Portfolio Value", "Benchmark Value"]],
        y_label="Value ($)",
    )

    detail_1, detail_2 = st.columns(2)
    final_cash = result["Portfolio History"][-1]["Cash"]
    detail_1.metric("Cash Remaining", f"${final_cash:,.2f}")
    detail_2.metric("Cache Status", result["Market Data Cache"]["Status"])

    st.subheader("Final Holdings")
    holdings_table = pd.DataFrame(result["Final Holdings"])
    if holdings_table.empty:
        st.info("This test finished with no stock holdings.")
    else:
        holdings_table["Shares"] = holdings_table["Shares"].round(4)
        holdings_table["Final Price"] = holdings_table["Final Price"].map(
            lambda value: "N/A" if pd.isna(value) else f"${value:,.2f}"
        )
        holdings_table["Market Value"] = holdings_table["Market Value"].map(
            lambda value: "N/A" if pd.isna(value) else f"${value:,.2f}"
        )
        st.dataframe(holdings_table, width="stretch", hide_index=True)

    st.subheader("Trade History")
    trade_table = pd.DataFrame(result["Trades"])
    if trade_table.empty:
        st.info("The strategy made no trades in this test.")
    else:
        trade_table["Date"] = pd.to_datetime(trade_table["Date"]).dt.date
        trade_table["Shares"] = trade_table["Shares"].round(4)
        trade_table["Price"] = trade_table["Price"].map(
            lambda value: f"${value:,.2f}"
        )
        trade_table["Fee"] = trade_table["Fee"].map(
            lambda value: f"${value:,.2f}"
        )
        st.dataframe(trade_table, width="stretch", hide_index=True)
