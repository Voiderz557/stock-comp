"""ML Research UI - Research Only. Not used for live trading.

This page builds historical feature/label datasets (`ml.dataset`) and runs
walk-forward validation of simple classification models (`ml.models`,
`ml.evaluation`) that rank stocks by predicted probability of beating a
benchmark. It never places trades, never touches paper trading, and is
completely separate from `app/competition_dashboard.py`.

Launch with:

    python -m streamlit run app/ml_research_ui.py
"""

import pandas as pd
import streamlit as st

from data.historical_universe import HISTORICAL_UNIVERSE_START
from data.scanner_universe import NASDAQ_100_TEST_UNIVERSE
from ml.dataset import (
    build_feature_dataset,
    build_quality_report,
    get_supervised_subset,
)
from ml.evaluation import aggregate_fold_metrics, run_walk_forward_evaluation
from ml.labels import FIXED_CLASSIFICATION_COLUMNS
from ml.models import (
    MODEL_BUILDERS,
    extract_feature_importance,
    infer_feature_columns,
)
from ml.validation import EXPANDING_WINDOW, ROLLING_WINDOW, generate_walk_forward_folds

st.set_page_config(page_title="ML Research Lab", layout="wide")
st.title("ML Research Lab")
st.warning(
    "**Research Only - Not Used for Live Trading.** This page ranks stocks "
    "for research purposes. It never places paper trades and is completely "
    "separate from strategy selection or the competition dashboard.",
    icon="🔬",
)

first_supported_date = HISTORICAL_UNIVERSE_START.date()
today = pd.Timestamp.today().normalize().date()

for key, default in {
    "ml_dataset": None,
    "ml_quality_report": None,
    "ml_fold_results": [],
    "ml_aggregate_metrics": {},
    "ml_feature_columns": [],
    "ml_categorical_columns": [],
}.items():
    if key not in st.session_state:
        st.session_state[key] = default

# ---------------------------------------------------------------------------
# 1. Build / load the dataset
# ---------------------------------------------------------------------------
st.header("1. Build Feature Dataset")

with st.form("dataset_settings"):
    date_col_1, date_col_2 = st.columns(2)
    with date_col_1:
        start_date = st.date_input(
            "Start Date",
            value=max(pd.Timestamp("2022-01-01").date(), first_supported_date),
            min_value=first_supported_date,
            max_value=today,
        )
    with date_col_2:
        end_date = st.date_input(
            "End Date",
            value=min(pd.Timestamp("2024-12-31").date(), today),
            min_value=first_supported_date,
            max_value=today,
        )

    settings_col_1, settings_col_2, settings_col_3 = st.columns(3)
    with settings_col_1:
        rebalance_frequency = st.selectbox(
            "Rebalance Frequency", options=["weekly", "daily", "monthly"], index=0
        )
    with settings_col_2:
        label_horizon = st.selectbox(
            "Label Horizon (trading days)", options=[5, 20, 60], index=1
        )
    with settings_col_3:
        use_point_in_time_universe = st.checkbox(
            "Use point-in-time Nasdaq-100 universe", value=True
        )

    universe = None
    if not use_point_in_time_universe:
        universe = st.multiselect(
            "Fixed Universe (used for every evaluation date)",
            options=NASDAQ_100_TEST_UNIVERSE,
            default=NASDAQ_100_TEST_UNIVERSE[:15],
        )

    include_strategy_features = st.checkbox(
        "Include existing-strategy score/signal features", value=True
    )

    build_button = st.form_submit_button(
        "Build / Rebuild Dataset", type="primary", use_container_width=True
    )

if build_button:
    st.session_state.ml_fold_results = []
    st.session_state.ml_aggregate_metrics = {}
    if pd.Timestamp(start_date) >= pd.Timestamp(end_date):
        st.error("Start Date must be before End Date.")
    elif use_point_in_time_universe and pd.Timestamp(start_date) < HISTORICAL_UNIVERSE_START:
        st.error(
            f"Requested observation start {start_date} is before "
            f"{HISTORICAL_UNIVERSE_START.date()}, the earliest supported Nasdaq-100 "
            "membership date. Price warmup may go earlier; observation dates may not."
        )
    else:
        progress = st.progress(0, text="Building dataset...")

        def _progress(completed, total, current_date):
            progress.progress(
                completed / total, text=f"Evaluating {pd.Timestamp(current_date).date()}..."
            )

        try:
            dataset = build_feature_dataset(
                start_date,
                end_date,
                universe=universe,
                rebalance_frequency=rebalance_frequency,
                label_horizon=int(label_horizon),
                include_strategy_features=include_strategy_features,
                progress_callback=_progress,
            )
            st.session_state.ml_dataset = dataset
            st.session_state.ml_quality_report = build_quality_report(dataset)
            st.session_state.ml_fold_results = []
            st.session_state.ml_aggregate_metrics = {}
            progress.progress(1.0, text="Dataset build complete.")
            st.success(f"Built {len(dataset):,} rows.")
        except Exception as error:
            st.error(f"Dataset build failed: {error}")

dataset = st.session_state.ml_dataset

if dataset is not None and not dataset.empty:
    st.subheader("Dataset Shape")
    shape_col_1, shape_col_2, shape_col_3 = st.columns(3)
    shape_col_1.metric("Rows", f"{len(dataset):,}")
    shape_col_2.metric("Columns", f"{dataset.shape[1]:,}")
    shape_col_3.metric("Unique Tickers", f"{dataset['Ticker'].nunique():,}")

    with st.expander("Preview Rows", expanded=False):
        st.dataframe(dataset.head(50), width="stretch", hide_index=True)

    with st.expander("Data Quality Report", expanded=False):
        report = st.session_state.ml_quality_report or {}
        st.write(f"**Date Range:** {report.get('Date Range')}")
        st.write(
            "**Rows excluded (insufficient history):** "
            f"{report.get('Rows Excluded - Insufficient History')}"
        )
        st.write(
            "**Rows with insufficient future label data:** "
            f"{report.get('Rows With Insufficient Future Label Data')}"
        )
        st.write(f"**Class balance (beats_SPY_20D):** {report.get('Class Balance')}")

        missing = pd.Series(report.get("Missing Value Counts", {}))
        missing = missing[missing > 0].sort_values(ascending=False)
        if not missing.empty:
            st.write("**Columns with missing values:**")
            st.dataframe(
                missing.rename("Missing Count").to_frame(),
                width="stretch",
            )
        else:
            st.write("No missing values.")

        failures = report.get("Failed Ticker/Date Calculations", [])
        if failures:
            st.write(f"**{len(failures)} failed ticker/date calculation(s):**")
            st.dataframe(pd.DataFrame(failures), width="stretch", hide_index=True)

    csv_bytes = dataset.to_csv(index=False).encode("utf-8")
    st.download_button(
        "Export Dataset (CSV)",
        data=csv_bytes,
        file_name="ml_feature_dataset.csv",
        mime="text/csv",
    )

    # -----------------------------------------------------------------
    # 2. Walk-forward validation
    # -----------------------------------------------------------------
    st.header("2. Walk-Forward Validation")

    available_targets = [
        column for column in FIXED_CLASSIFICATION_COLUMNS if column in dataset.columns
    ]
    with st.form("validation_settings"):
        model_col, target_col = st.columns(2)
        with model_col:
            model_name = st.selectbox("Model", options=list(MODEL_BUILDERS))
        with target_col:
            target_column = st.selectbox(
                "Target",
                options=available_targets,
                index=available_targets.index("beats_SPY_20D")
                if "beats_SPY_20D" in available_targets
                else 0,
            )

        window_col, min_train_col, validation_col, step_col = st.columns(4)
        with window_col:
            window_mode = st.selectbox(
                "Window Mode", options=[EXPANDING_WINDOW, ROLLING_WINDOW]
            )
        with min_train_col:
            min_train_days = st.number_input(
                "Min Train Days", min_value=30, value=365, step=30
            )
        with validation_col:
            validation_days = st.number_input(
                "Validation Days", min_value=7, value=90, step=7
            )
        with step_col:
            step_days = st.number_input("Step Days", min_value=7, value=90, step=7)

        strategy_score_columns = [
            column for column in dataset.columns if column.startswith("Strategy Score: ")
        ]
        strategy_baseline_column = st.selectbox(
            "Compare Against Existing Strategy (optional)",
            options=["None"] + strategy_score_columns,
        )

        run_validation_button = st.form_submit_button(
            "Run Walk-Forward Validation", type="primary", use_container_width=True
        )

    if run_validation_button:
        try:
            supervised = get_supervised_subset(dataset, target_column)
            feature_columns, categorical_columns = infer_feature_columns(supervised)
            folds = generate_walk_forward_folds(
                supervised["Date"].min(),
                supervised["Date"].max(),
                min_train_days=int(min_train_days),
                validation_days=int(validation_days),
                step_days=int(step_days),
                window_mode=window_mode,
            )
            st.info(
                f"**Requested dataset range:** {supervised['Date'].min().date()} → "
                f"{supervised['Date'].max().date()}  \n"
                f"**Valid fold count:** {len(folds)}  \n"
                + (
                    f"**First train:** {folds[0].train_start.date()} → {folds[0].train_end.date()}  \n"
                    f"**First validation:** {folds[0].validation_start.date()} → {folds[0].validation_end.date()}"
                    if folds
                    else "**No valid folds** — requested range is too short for these train/validation/step sizes."
                )
            )
            if not folds:
                st.warning(
                    "No walk-forward folds fit in this date range with the "
                    "chosen train/validation/step sizes."
                )
            else:
                model_builder = MODEL_BUILDERS[model_name]
                fold_results = run_walk_forward_evaluation(
                    supervised,
                    folds,
                    model_builder,
                    target_column=target_column,
                    feature_columns=feature_columns,
                    categorical_columns=categorical_columns,
                    strategy_score_column=(
                        None
                        if strategy_baseline_column == "None"
                        else strategy_baseline_column
                    ),
                )
                st.session_state.ml_fold_results = fold_results
                st.session_state.ml_aggregate_metrics = aggregate_fold_metrics(fold_results)
                st.session_state.ml_feature_columns = feature_columns
                st.session_state.ml_categorical_columns = categorical_columns
                if fold_results:
                    st.success(f"Evaluated {len(fold_results)} fold(s).")
                else:
                    st.warning(
                        "No fold produced a valid train/validation split "
                        "(often caused by too few positive/negative examples)."
                    )
        except Exception as error:
            st.error(f"Walk-forward validation failed: {error}")

    fold_results = st.session_state.ml_fold_results
    if fold_results:
        st.subheader("Fold Metrics")
        fold_rows = []
        for fold in fold_results:
            row = {
                "Fold": fold["Fold"],
                "Train End": pd.Timestamp(fold["Train End"]).date(),
                "Validation Start": pd.Timestamp(fold["Validation Start"]).date(),
                "Validation End": pd.Timestamp(fold["Validation End"]).date(),
                "Train Rows": fold["Train Rows"],
                "Validation Rows": fold["Validation Rows"],
                **fold["Classification Metrics"],
            }
            fold_rows.append(row)
        st.dataframe(pd.DataFrame(fold_rows), width="stretch", hide_index=True)

        st.subheader("Aggregate Metrics (Averaged Across Folds)")
        aggregate = st.session_state.ml_aggregate_metrics
        st.dataframe(
            pd.DataFrame([aggregate.get("Classification Metrics", {})]),
            width="stretch",
            hide_index=True,
        )

        st.subheader("Top-Stock Ranking Performance")
        st.caption(
            "Average forward 20D return if you had bought the model's top-N "
            "predicted stocks at each rebalance date, vs. a random-N baseline "
            "and (if selected) an existing strategy's own ranking."
        )
        for n, model_top_n in aggregate.get("Top N Metrics", {}).items():
            with st.expander(f"Top {n} Stocks", expanded=(n == 5)):
                comparison_rows = [{"Ranking": "Model", **model_top_n}]
                first_fold = fold_results[0]
                random_metrics = first_fold.get("Random Baseline Top N Metrics") or {}
                if n in random_metrics:
                    # Average the random baseline across folds too, for a fair comparison.
                    random_values = [
                        fold["Random Baseline Top N Metrics"][n]
                        for fold in fold_results
                        if fold.get("Random Baseline Top N Metrics")
                    ]
                    random_df = pd.DataFrame(random_values)
                    comparison_rows.append(
                        {"Ranking": "Random Baseline", **random_df.mean(numeric_only=True).to_dict()}
                    )
                strategy_values = [
                    fold["Strategy Baseline Top N Metrics"][n]
                    for fold in fold_results
                    if fold.get("Strategy Baseline Top N Metrics")
                    and fold["Strategy Baseline Top N Metrics"].get(n)
                ]
                if strategy_values:
                    strategy_df = pd.DataFrame(strategy_values)
                    comparison_rows.append(
                        {
                            "Ranking": "Existing Strategy",
                            **strategy_df.mean(numeric_only=True).to_dict(),
                        }
                    )
                st.dataframe(pd.DataFrame(comparison_rows), width="stretch", hide_index=True)

        st.subheader("Feature Importance")
        st.caption("From the most recent fold's fitted model.")
        try:
            importance_table = extract_feature_importance(fold_results[-1]["Pipeline"])
            st.dataframe(importance_table, width="stretch", hide_index=True)
        except Exception as error:
            st.info(f"Feature importance unavailable: {error}")

        fold_export_rows = []
        for fold in fold_results:
            fold_export_rows.append(
                {
                    "Fold": fold["Fold"],
                    "Validation Start": fold["Validation Start"],
                    "Validation End": fold["Validation End"],
                    **fold["Classification Metrics"],
                }
            )
        st.download_button(
            "Export Fold Metrics (CSV)",
            data=pd.DataFrame(fold_export_rows).to_csv(index=False).encode("utf-8"),
            file_name="ml_walk_forward_fold_metrics.csv",
            mime="text/csv",
        )
else:
    st.info("Build a dataset above to begin.")
