"""ML vs Strategy Benchmark - Research Only.

Determines whether the `ml` package's ranking models (Logistic Regression,
Random Forest) outperform the existing rule-based strategies enough to
justify future paper-trading integration. Every method here - ML rankings,
every rule-based strategy, and the Regime Switching meta-strategy - is
evaluated on the exact same set of historical periods
(`backtesting.periods.generate_random_periods`), using the same $100,000 /
$20,000-per-stock / $5-minimum-price / long-only / cash-allowed portfolio
constraints as the main backtester (`backtesting.engine`, reused unmodified
via `benchmarking.simulation`).

This page never places a trade. It produces a `PROMOTE` / `RESEARCH_MORE` /
`REJECT` research recommendation only - nothing here connects a promoted
model to paper trading or live trading.

Launch with:

    python -m streamlit run app/ml_benchmark_ui.py
"""

import pandas as pd
import streamlit as st

from data.historical_universe import HISTORICAL_UNIVERSE_START
from data.scanner_universe import NASDAQ_100_TEST_UNIVERSE
from backtesting.periods import DURATION_OPTIONS
from benchmarking.ml_training import TRAINING_WINDOW_CALENDAR_DAYS, build_training_window
from config import (
    BACKTEST_BENCHMARK,
    BACKTEST_FEE_RATE,
    BACKTEST_STARTING_CASH,
    MAX_POSITION_VALUE,
    MIN_STOCK_PRICE,
)
from ml.models import MODEL_BUILDERS

from benchmarking.export import build_benchmark_zip
from benchmarking.metrics import COMPETITION_RETURN_THRESHOLDS
from benchmarking.promotion import INVALID, PROMOTE, REJECT, RESEARCH_MORE
from benchmarking.runner import REQUESTED_RULE_BASED_STRATEGIES, run_benchmark

st.set_page_config(page_title="ML vs Strategy Benchmark", layout="wide")
st.title("ML vs Strategy Benchmark")
st.warning(
    "**Research Only.** This page only produces a research recommendation "
    "(PROMOTE / RESEARCH_MORE / REJECT). It never places a paper or live "
    "trade and is completely separate from `app/competition_dashboard.py`.",
    icon="🔬",
)

first_supported_date = HISTORICAL_UNIVERSE_START.date()
today = pd.Timestamp.today().normalize().date()
default_earliest = max(pd.Timestamp("2023-01-01").date(), first_supported_date)
default_latest = min(pd.Timestamp("2025-12-31").date(), today)

for key, default in {"benchmark_result": None}.items():
    if key not in st.session_state:
        st.session_state[key] = default

# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
with st.form("benchmark_settings"):
    st.subheader("Evaluation Periods")
    period_col_1, period_col_2, period_col_3 = st.columns(3)
    with period_col_1:
        duration = st.selectbox(
            "Test Duration",
            options=list(DURATION_OPTIONS),
            index=list(DURATION_OPTIONS).index("5 months"),
        )
    with period_col_2:
        earliest_allowed = st.date_input(
            "Earliest Allowed Date",
            value=default_earliest,
            min_value=first_supported_date,
            max_value=today,
        )
    with period_col_3:
        latest_allowed = st.date_input(
            "Latest Allowed Date",
            value=default_latest,
            min_value=first_supported_date,
            max_value=today,
        )

    period_col_4, period_col_5 = st.columns(2)
    with period_col_4:
        number_of_tests = st.number_input(
            "Number of Tests",
            min_value=1,
            value=5,
            step=1,
            help="Robustness testing: more tests give a more reliable read "
            "on whether performance depends on a few extreme winners. "
            "Any positive integer is allowed; there is no maximum.",
        )
    with period_col_5:
        random_seed = st.number_input("Random Seed", value=42, step=1)

    st.subheader("Methods to Compare")
    method_col_1, method_col_2 = st.columns(2)
    with method_col_1:
        requested_strategies = st.multiselect(
            "Rule-Based Strategies",
            options=list(REQUESTED_RULE_BASED_STRATEGIES),
            default=list(REQUESTED_RULE_BASED_STRATEGIES),
            help="Strategies not yet implemented in `strategies.registry` "
            "are reported separately as unavailable, not silently skipped.",
        )
        include_regime_switching = st.checkbox(
            "Include Regime Switching (delegates to whichever registered "
            "strategy `market.strategy_selector` prefers each week)",
            value=True,
        )
    with method_col_2:
        ml_models = st.multiselect(
            "ML Models",
            options=list(MODEL_BUILDERS),
            default=list(MODEL_BUILDERS),
        )
        top_n_values = st.multiselect(
            "Top N (ML ranking portfolio size)",
            options=[5, 10, 20],
            default=[5, 10, 20],
        )

    with st.expander("Universe / Portfolio Settings", expanded=False):
        universe_col, cash_col = st.columns(2)
        with universe_col:
            use_point_in_time_universe = st.checkbox(
                "Use point-in-time Nasdaq-100 universe", value=True
            )
            universe = None
            if not use_point_in_time_universe:
                universe = st.multiselect(
                    "Fixed Universe",
                    options=NASDAQ_100_TEST_UNIVERSE,
                    default=NASDAQ_100_TEST_UNIVERSE[:15],
                )
        with cash_col:
            starting_cash = st.number_input(
                "Starting Cash ($)", min_value=0.01, value=float(BACKTEST_STARTING_CASH), step=10_000.0
            )
            max_position_value = st.number_input(
                "Max $ per Stock", min_value=0.01, value=float(MAX_POSITION_VALUE), step=1_000.0
            )
            min_stock_price = st.number_input(
                "Min Stock Price ($)", min_value=0.0, value=float(MIN_STOCK_PRICE), step=1.0
            )
            benchmark = st.text_input("Benchmark", value=BACKTEST_BENCHMARK)

    run_button = st.form_submit_button(
        "Run Benchmark", type="primary", use_container_width=True
    )

if run_button:
    st.session_state.benchmark_result = None
    requested_start = pd.Timestamp(earliest_allowed)
    requested_end = pd.Timestamp(latest_allowed)
    if requested_start >= requested_end:
        st.error("Earliest allowed date must be before latest allowed date.")
    elif universe is None and requested_start < HISTORICAL_UNIVERSE_START:
        st.error(
            f"Requested evaluation start {requested_start.date()} is before "
            f"{HISTORICAL_UNIVERSE_START.date()}, the earliest supported Nasdaq-100 "
            "membership date. Choose a later start, or use a fixed universe."
        )
    else:
        try:
            from backtesting.periods import generate_random_periods

            planned_periods = generate_random_periods(
                duration, requested_start, requested_end, int(number_of_tests), int(random_seed)
            )
            eval_start = min(start for start, _ in planned_periods)
            eval_end = max(end for _, end in planned_periods)
            train_start, train_end = build_training_window(
                eval_start,
                HISTORICAL_UNIVERSE_START if universe is None else None,
                TRAINING_WINDOW_CALENDAR_DAYS,
            )
            st.info(
                f"**Requested range:** {requested_start.date()} → {requested_end.date()}  \n"
                f"**Effective evaluation range:** {eval_start.date()} → {eval_end.date()}  \n"
                f"**Training window (first period):** {train_start.date()} → {train_end.date()}  \n"
                f"**Validation window (first period):** {eval_start.date()} → {planned_periods[0][1].date()}  \n"
                f"**Test count:** {len(planned_periods)}  \n"
                f"**Seed:** {int(random_seed)}  \n"
                f"**Universe:** {'point-in-time Nasdaq-100' if universe is None else ', '.join(universe)}"
            )
            progress = st.progress(0, text="Starting benchmark...")

            def _progress(event):
                if not isinstance(event, dict):
                    return
                completed = event.get("completed") or 0
                total = event.get("total") or 1
                stage = event.get("stage") or ""
                if stage == "complete":
                    fraction = 1.0
                else:
                    finished = max(0, completed - 1)
                    fraction = finished / total if total else 0.0
                elapsed = event.get("elapsed_seconds") or 0.0
                period_elapsed = event.get("period_elapsed_seconds")
                bits = []
                if completed:
                    bits.append(f"Period {completed}/{total}")
                else:
                    bits.append(f"Setup ({total} period(s))")
                if event.get("period_start") and event.get("period_end"):
                    bits.append(f"{event['period_start']} to {event['period_end']}")
                if stage:
                    bits.append(stage)
                if event.get("method"):
                    bits.append(str(event["method"]))
                if event.get("detail"):
                    bits.append(str(event["detail"]))
                bits.append(f"{elapsed:.0f}s elapsed")
                if completed and period_elapsed is not None:
                    bits.append(f"{period_elapsed:.0f}s this period")
                progress.progress(min(1.0, max(0.0, fraction)), text=" | ".join(bits))

            result = run_benchmark(
                duration=duration,
                earliest_allowed=earliest_allowed,
                latest_allowed=latest_allowed,
                number_of_tests=int(number_of_tests),
                random_seed=int(random_seed),
                requested_strategies=tuple(requested_strategies),
                include_regime_switching=include_regime_switching,
                ml_models=tuple(ml_models),
                top_n_values=tuple(top_n_values) or (5, 10, 20),
                benchmark=benchmark.strip().upper(),
                starting_cash=float(starting_cash),
                max_position_value=float(max_position_value),
                min_stock_price=float(min_stock_price),
                fee_rate=BACKTEST_FEE_RATE,
                universe=universe,
                progress_callback=_progress,
            )
            st.session_state.benchmark_result = result
            progress.progress(1.0, text="Benchmark complete.")
            if result.errors:
                st.warning(f"{len(result.errors)} method/period combination(s) failed - see below.")
            else:
                st.success("Benchmark complete with no method/period failures.")
        except Exception as error:
            st.error(f"Benchmark failed: {error}")

result = st.session_state.benchmark_result

if result is None:
    st.info("Configure settings above and click **Run Benchmark** to begin.")
    st.stop()

cfg = result.config or {}
st.caption(
    f"Requested {cfg.get('earliest_allowed')} → {cfg.get('latest_allowed')} | "
    f"Effective {cfg.get('evaluation_start_date')} → {cfg.get('evaluation_end_date')} | "
    f"Training floor {cfg.get('effective_training_start_floor')} | "
    f"Tests {cfg.get('number_of_tests')} | Seed {cfg.get('random_seed')} | "
    f"Valid ML folds {cfg.get('ml_fold_summary')} | "
    f"Benchmark valid {cfg.get('benchmark_valid')}"
)

if result.unavailable_strategies:
    st.info(
        "Not yet implemented, so excluded from this run: "
        + ", ".join(result.unavailable_strategies)
    )

# ---------------------------------------------------------------------------
# Historical data coverage (missing provider rows are not dropped from the universe)
# ---------------------------------------------------------------------------
st.header("Historical Data Coverage")
coverage = result.data_coverage or {}
coverage_failures = coverage.get("Data Source Failures") or []
unavailable_constituents = coverage.get("Unavailable Valid Constituents") or []
if coverage.get("Coverage Is Valid", not coverage_failures):
    st.success("Required historical constituents have provider coverage for this run.")
else:
    st.error(
        "Required historical price data is missing for one or more historically "
        "valid constituents. Those tickers were kept in the universe; affected "
        "results are not valid or promotable."
    )
    if unavailable_constituents:
        st.write(
            "**Unavailable valid constituents:** " + ", ".join(unavailable_constituents)
        )
    if coverage_failures:
        st.dataframe(pd.DataFrame(coverage_failures), width="stretch", hide_index=True)
    diagnosis = coverage.get("Coverage Diagnosis") or []
    if diagnosis:
        st.subheader("Coverage diagnosis")
        st.dataframe(pd.DataFrame(diagnosis), width="stretch", hide_index=True)

stage_timings = cfg.get("stage_timings") or []
if stage_timings:
    with st.expander("Stage timings", expanded=False):
        st.dataframe(pd.DataFrame(stage_timings), width="stretch", hide_index=True)

# ---------------------------------------------------------------------------
# Leakage audit (shown first - an INVALID audit means no promotion recommendation)
# ---------------------------------------------------------------------------
st.header("Leakage Audit")
audit = result.leakage_audit
if audit["Is Valid"]:
    st.success("VALID - every automated leakage check passed.")
else:
    st.error(
        "INVALID - at least one leakage check failed. No promotion "
        "recommendation is produced; every ML method below is forced to INVALID."
    )
audit_table = pd.DataFrame(audit["Checks"])
st.dataframe(audit_table, width="stretch", hide_index=True)

# ---------------------------------------------------------------------------
# Aggregate benchmark summary
# ---------------------------------------------------------------------------
st.header("Aggregate Benchmark Summary")
aggregate_table = result.aggregate_table
if aggregate_table.empty:
    st.warning("No method produced a valid result for these settings.")
else:
    highlight_column = "P(Return >= 20%)"
    display_columns = [
        "Method",
        "Periods Tested",
        "Total Return",
        "Annualized Return",
        "Average Return",
        "Median Return",
        "Average Excess Return",
        "Median Excess Return",
        "Beat SPY %",
        "Positive Period %",
        "Max Drawdown",
        "Volatility",
        "Sharpe Ratio",
        "Sortino Ratio",
        "Trade Count",
        "Turnover",
        "Best Period Return",
        "Worst Period Return",
        highlight_column,
        "Extreme Winner Dependent",
    ]
    display_columns = [column for column in display_columns if column in aggregate_table.columns]
    styled = aggregate_table[display_columns].style.background_gradient(
        subset=[highlight_column] if highlight_column in display_columns else [],
        cmap="Greens",
    )
    st.caption(f"**{highlight_column}** is the headline competition metric - highlighted above.")
    if not cfg.get("coverage_is_valid", True) or not cfg.get("leakage_audit_is_valid", True):
        st.warning(
            "These tables are shown for diagnosis only. Coverage or leakage "
            "failures make this run invalid; nothing here is promotable."
        )
    st.dataframe(styled, width="stretch", hide_index=True)

    with st.expander("Robustness Statistics (Trimmed Mean / Percentiles)", expanded=False):
        robustness_columns = [
            "Method",
            "Trimmed Mean Return",
            "Median Return",
            "Percentile 10",
            "Percentile 25",
            "Percentile 75",
            "Percentile 90",
            "Extreme Winner Dependent",
        ]
        robustness_columns = [c for c in robustness_columns if c in aggregate_table.columns]
        st.dataframe(aggregate_table[robustness_columns], width="stretch", hide_index=True)

    with st.expander("Every Competition Threshold Probability", expanded=False):
        threshold_columns = ["Method"] + [
            f"P(Return >= {int(round(threshold * 100))}%)" for threshold in COMPETITION_RETURN_THRESHOLDS
        ]
        threshold_columns = [c for c in threshold_columns if c in aggregate_table.columns]
        st.dataframe(aggregate_table[threshold_columns], width="stretch", hide_index=True)

# ---------------------------------------------------------------------------
# Period-by-period detail
# ---------------------------------------------------------------------------
st.header("Period-by-Period Results")
if result.period_table.empty:
    st.warning("No period results to show.")
else:
    method_filter = st.multiselect(
        "Filter by method",
        options=sorted(result.period_table["Method"].unique()),
        default=[],
    )
    period_table = result.period_table
    if method_filter:
        period_table = period_table[period_table["Method"].isin(method_filter)]
    st.dataframe(period_table, width="stretch", hide_index=True)

if result.errors:
    with st.expander(f"{len(result.errors)} Method/Period Failure(s)", expanded=False):
        st.dataframe(pd.DataFrame({"Error": result.errors}), width="stretch", hide_index=True)

# ---------------------------------------------------------------------------
# ML stability
# ---------------------------------------------------------------------------
st.header("ML Model Stability")
if result.feature_stability_summary.empty:
    st.info("No ML fold trained successfully, so no stability metrics are available.")
else:
    st.dataframe(result.feature_stability_summary, width="stretch", hide_index=True)
    for _, row in result.feature_stability_summary.iterrows():
        if row.get("Unstable Feature Importance"):
            st.warning(
                f"**{row['Model']}**: feature importance changes wildly across folds "
                f"(average top-feature similarity = {row['Average Top-Feature Jaccard Similarity']:.2f})."
            )

    with st.expander("Feature Importance Detail (long format, every fold)", expanded=False):
        st.dataframe(result.feature_stability_table, width="stretch", hide_index=True)

    with st.expander("Train / Validation Sample Sizes and Class Balance per Fold", expanded=False):
        st.dataframe(result.fold_metrics_table, width="stretch", hide_index=True)

# ---------------------------------------------------------------------------
# Promotion recommendation
# ---------------------------------------------------------------------------
st.header("Promotion Recommendation")
st.caption(
    "This is a research recommendation only. Promoted models are NOT "
    "automatically connected to paper trading or live trading."
)
if result.promotion_table.empty:
    st.info("No ML method produced enough results to evaluate for promotion.")
else:
    decision_icon = {PROMOTE: "✅", RESEARCH_MORE: "🟡", REJECT: "🛑", INVALID: "🚫"}
    for promotion_result in result.promotion_results:
        icon = decision_icon.get(promotion_result.decision, "")
        with st.expander(
            f"{icon} {promotion_result.method}: {promotion_result.decision} "
            f"(vs. {promotion_result.compared_against})",
            expanded=(promotion_result.decision != RESEARCH_MORE),
        ):
            for reason in promotion_result.reasons:
                st.write(f"- {reason}")
    st.dataframe(result.promotion_table, width="stretch", hide_index=True)

# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------
st.header("Export")
if not result.leakage_audit.get("Is Valid", False):
    st.warning("This package is marked INVALID because the leakage audit failed.")
if not cfg.get("coverage_is_valid", True):
    st.warning(
        "This package is marked INVALID because required historical price "
        "coverage is incomplete. Delisted/historical constituents were not removed."
    )
zip_bytes = build_benchmark_zip(result)
st.download_button(
    "Download Full Benchmark Package (ZIP)",
    data=zip_bytes,
    file_name="ml_benchmark_results.zip",
    mime="application/zip",
    type="primary",
)
