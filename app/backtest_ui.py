from datetime import datetime, timezone

import pandas as pd
import streamlit as st

from backtesting.engine import run_backtest
from backtesting.export import (
    build_comparison_summary,
    build_complete_backtest_package,
    build_test_summary,
)
from backtesting.periods import DURATION_OPTIONS, generate_random_periods
from config import (
    BACKTEST_BENCHMARK,
    BACKTEST_FEE_RATE,
    BACKTEST_STARTING_CASH,
    MAX_POSITION_VALUE,
    MIN_STOCK_PRICE,
)
from data.historical_universe import SNAPSHOT_DATES
from data.market_data import clear_market_data_cache
from strategies.registry import available_strategy_names, get_strategy


st.set_page_config(page_title="Stock Strategy Backtester", layout="wide")
st.title("Stock Strategy Backtester")
st.write(
    "Test one algorithm or compare algorithms across the exact same random "
    "market periods."
)

strategy_names = available_strategy_names()
mode = st.radio(
    "Mode",
    options=["Test One Algorithm", "Compare Algorithms"],
    horizontal=True,
)

first_supported_date = SNAPSHOT_DATES[0].date()
today = pd.Timestamp.today().normalize().date()
default_earliest = max(pd.Timestamp("2022-01-01").date(), first_supported_date)
default_latest = min(pd.Timestamp("2025-12-31").date(), today)

with st.form("backtest_settings"):
    if mode == "Test One Algorithm":
        selected_strategies = [
            st.selectbox("Algorithm", options=strategy_names)
        ]
    else:
        selected_strategies = st.multiselect(
            "Algorithms",
            options=strategy_names,
            default=strategy_names,
            help="Every algorithm uses the same generated periods and settings.",
        )

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
            help="The same seed and settings reproduce the same periods.",
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

for key, default in {
    "backtest_results": [],
    "backtest_failures": [],
    "backtest_periods": [],
    "completed_backtest_settings": {},
}.items():
    if key not in st.session_state:
        st.session_state[key] = default

if run_button:
    try:
        if not selected_strategies:
            raise ValueError("Select at least one algorithm.")
        if not benchmark.strip():
            raise ValueError("Benchmark ticker cannot be empty.")

        # Generate exactly once, then reuse for every selected strategy.
        periods = generate_random_periods(
            duration,
            earliest_allowed,
            latest_allowed,
            int(number_of_tests),
            int(random_seed),
        )
        strategy_parameters = {
            name: get_strategy(name).parameters for name in selected_strategies
        }
        settings = {
            "selected_strategies": selected_strategies,
            "mode": mode,
            "random_seed": int(random_seed),
            "test_duration": duration,
            "earliest_boundary": earliest_allowed,
            "latest_boundary": latest_allowed,
            "exact_generated_periods": [
                {"test": index, "start": start, "end": end}
                for index, (start, end) in enumerate(periods, start=1)
            ],
            "starting_cash": float(starting_cash),
            "benchmark": benchmark.strip().upper(),
            "minimum_stock_price": float(min_stock_price),
            "maximum_initial_allocation_per_stock": float(max_position_value),
            "transaction_fee_slippage": fee_percent / 100,
            "strategy_parameters": strategy_parameters,
            "generated_at_utc": datetime.now(timezone.utc),
            "package_version": 1,
        }

        progress = st.progress(0, text="Preparing tests...")
        phase_status = st.empty()
        cache_status = st.empty()
        completed_results = []
        failed_tests = []
        total_jobs = len(selected_strategies) * len(periods)
        completed_jobs = 0

        for strategy_name in selected_strategies:
            for test_number, (period_start, period_end) in enumerate(
                periods, start=1
            ):
                job_label = (
                    f"{strategy_name} - Test {test_number}/{number_of_tests}"
                )

                def show_cache_status(status, ticker, label=job_label):
                    cache_status.info(f"{label} - {status}: {ticker}")

                def show_phase(phase, label=job_label):
                    phase_status.info(f"{label}\n\n{phase}")
                    progress.progress(
                        completed_jobs / total_jobs,
                        text=f"{label} - {phase}",
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
                        strategy_name=strategy_name,
                    )
                    result["Test"] = test_number
                    completed_results.append(result)
                    completed_jobs += 1
                    phase_status.success(f"{job_label}\n\nComplete")
                    progress.progress(
                        completed_jobs / total_jobs,
                        text=f"{job_label} - Complete",
                    )
                except Exception as error:
                    completed_jobs += 1
                    failed_tests.append(
                        {
                            "Algorithm": strategy_name,
                            "Test": test_number,
                            "Requested Start": period_start.date(),
                            "Requested End": period_end.date(),
                            "Error": str(error),
                        }
                    )
                    phase_status.error(f"{job_label}\n\nFailed - continuing")
                    progress.progress(
                        completed_jobs / total_jobs,
                        text=f"{job_label} - Failed; continuing...",
                    )

        st.session_state.backtest_results = completed_results
        st.session_state.backtest_failures = failed_tests
        st.session_state.backtest_periods = periods
        st.session_state.completed_backtest_settings = settings
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

    st.subheader("Algorithm Comparison")
    comparison = build_comparison_summary(results)
    st.dataframe(
        comparison.style.format(
            {
                "Average Return": "{:+.2%}",
                "Median Return": "{:+.2%}",
                "Average Benchmark Return": "{:+.2%}",
                "Average Excess Return": "{:+.2%}",
                "Win Rate vs Benchmark": "{:.0%}",
                "Average Number of Trades": "{:.1f}",
            }
        ),
        width="stretch",
        hide_index=True,
    )

    st.subheader("Completed Tests")
    tests = build_test_summary(results)
    st.dataframe(
        tests.style.format(
            {
                "Strategy Return": "{:+.2%}",
                "Benchmark Return": "{:+.2%}",
                "Excess Return": "{:+.2%}",
            }
        ),
        width="stretch",
        hide_index=True,
    )

    package = build_complete_backtest_package(
        results,
        st.session_state.backtest_periods,
        st.session_state.completed_backtest_settings,
    )
    st.download_button(
        "Download Complete Backtest Package",
        data=package,
        file_name="complete_backtest_package.zip",
        mime="application/zip",
        type="primary",
    )

    st.subheader("Inspect Results")
    completed_algorithms = list(dict.fromkeys(result["Algorithm"] for result in results))
    inspected_algorithm = st.selectbox("Algorithm to Inspect", completed_algorithms)
    algorithm_results = [
        result for result in results if result["Algorithm"] == inspected_algorithm
    ]
    inspected_test = st.selectbox(
        "Test to Inspect",
        options=[result["Test"] for result in algorithm_results],
        format_func=lambda test: f"Test {test}",
    )
    result = next(
        item for item in algorithm_results if item["Test"] == inspected_test
    )

    st.caption(
        "Requested period: "
        f"{pd.Timestamp(result.get('Requested Start', result['Start Date'])).date()} to "
        f"{pd.Timestamp(result.get('Requested End', result['End Date'])).date()} | "
        "Actual trading dates: "
        f"{pd.Timestamp(result.get('Actual Start', result['Start Date'])).date()} to "
        f"{pd.Timestamp(result.get('Actual End', result['End Date'])).date()}"
    )

    if result["Universe Approximate"]:
        snapshot_dates = ", ".join(
            str(date.date()) for date in result["Universe Snapshot Dates"]
        )
        st.warning(
            "Historical Nasdaq-100 membership is approximate. "
            f"Snapshots used: {snapshot_dates}. {result['Universe Source']}"
        )

    history_table = pd.DataFrame(result["Portfolio History"]).set_index("Date")
    st.subheader("Portfolio vs Benchmark")
    st.line_chart(
        history_table[["Portfolio Value", "Benchmark Value"]],
        y_label="Value ($)",
    )

    detail_1, detail_2 = st.columns(2)
    detail_1.metric(
        "Cash Remaining", f"${result['Portfolio History'][-1]['Cash']:,.2f}"
    )
    detail_2.metric("Cache Status", result["Market Data Cache"]["Status"])

    st.subheader("Final Holdings")
    holdings = pd.DataFrame(result["Final Holdings"])
    if holdings.empty:
        st.info("This test finished with no stock holdings.")
    else:
        holdings["Shares"] = holdings["Shares"].round(4)
        holdings["Final Price"] = holdings["Final Price"].map(
            lambda value: "N/A" if pd.isna(value) else f"${value:,.2f}"
        )
        holdings["Market Value"] = holdings["Market Value"].map(
            lambda value: "N/A" if pd.isna(value) else f"${value:,.2f}"
        )
        st.dataframe(holdings, width="stretch", hide_index=True)

    st.subheader("Trade History")
    trades = pd.DataFrame(result["Trades"])
    if trades.empty:
        st.info("The strategy made no trades in this test.")
    else:
        trades["Date"] = pd.to_datetime(trades["Date"]).dt.date
        trades["Shares"] = trades["Shares"].round(4)
        trades["Price"] = trades["Price"].map(lambda value: f"${value:,.2f}")
        trades["Fee"] = trades["Fee"].map(lambda value: f"${value:,.2f}")
        st.dataframe(trades, width="stretch", hide_index=True)
