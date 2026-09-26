"""Top-level orchestration: same periods, every method, then metrics,
stability, leakage audit, and promotion decisions.

`run_benchmark(...)` is the single entry point used by both
`app/ml_benchmark_ui.py` and the test suite.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, replace

import pandas as pd

from backtesting.engine import download_backtest_data, get_ticker_data
from backtesting.periods import generate_random_periods
from config import (
    BACKTEST_BENCHMARK,
    BACKTEST_FEE_RATE,
    BACKTEST_STARTING_CASH,
    MAX_POSITION_VALUE,
    MIN_STOCK_PRICE,
    POSITION_LIMIT_MODE,
)
from data.coverage_diagnosis import diagnose_coverage_failures
from data.historical_universe import HISTORICAL_UNIVERSE_START_DATE, get_historical_universe
from data.market_data import load_market_data
from ml.feature_cache import reset_feature_cache
from market.regime import REQUIRED_HISTORY_DAYS as REGIME_REQUIRED_HISTORY_DAYS
from market.strategy_selector import describe_availability
from ml.features import REQUIRED_HISTORY_DAYS as ML_REQUIRED_HISTORY_DAYS
from ml.models import MODEL_BUILDERS
from strategies.registry import available_strategy_names, get_strategy

from benchmarking.leakage_audit import run_leakage_audit
from benchmarking.coverage import audit_price_coverage
from benchmarking.metrics import build_aggregate_table, build_period_results_table, build_period_row
from benchmarking.ml_training import (
    InsufficientTrainingDataError,
    TRAINING_WINDOW_CALENDAR_DAYS,
    fit_prepared_fold,
    prepare_training_fold,
)
from benchmarking.promotion import build_promotion_table, evaluate_promotion
from benchmarking.simulation import MLRankingStrategy, RegimeSwitchingStrategy, run_portfolio_simulation
from benchmarking.stability import (
    build_fold_metrics_table,
    compute_feature_stability,
    compute_prediction_distribution,
)

# The strategies Part 1 asks to benchmark, in the order requested. Any name
# not currently registered in `strategies.registry` is reported as
# unavailable (via `market.strategy_selector.describe_availability`) rather
# than silently skipped or fabricated.
REQUESTED_RULE_BASED_STRATEGIES = (
    "Baseline",
    "Momentum V2",
    "Aggressive Momentum V1",
    "Relative Strength Momentum V1",
    "Breakout Volume V1",
    "Mean Reversion V1",
)
DEFAULT_ML_MODELS = tuple(MODEL_BUILDERS)
DEFAULT_TOP_N_VALUES = (5, 10, 20)
REGIME_SWITCHING_METHOD_NAME = "Regime Switching"

# How many folds/tickers the leakage audit re-verifies per check - kept
# small so the audit stays fast even for 100-test robustness runs.
LEAKAGE_AUDIT_SAMPLE_UNIVERSE_SIZE = 8


@dataclass
class BenchmarkResult:
    periods: list
    period_table: pd.DataFrame
    aggregate_table: pd.DataFrame
    fold_metrics_table: pd.DataFrame
    feature_stability_table: pd.DataFrame
    feature_stability_summary: pd.DataFrame
    leakage_audit: dict
    promotion_table: pd.DataFrame
    promotion_results: list = field(default_factory=list)
    unavailable_strategies: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    data_coverage: dict = field(default_factory=dict)
    config: dict = field(default_factory=dict)


def _notify_progress(callback, event):
    if callback is None:
        return
    try:
        callback(event)
    except TypeError:
        callback(event.get("completed") or 0, event.get("total") or 1)


class _StageTracker:
    """Elapsed-time progress events for downloads, training, simulation, and audit."""

    def __init__(self, callback, total_periods):
        self.callback = callback
        self.total_periods = total_periods
        self.run_started = time.monotonic()
        self.period_started = self.run_started
        self.current_period = 0
        self.period_start = None
        self.period_end = None
        self.timings = []
        self._stage_started = self.run_started
        self._stage_name = "start"
        self._stage_method = None

    def _elapsed(self):
        return time.monotonic() - self.run_started

    def _period_elapsed(self):
        return time.monotonic() - self.period_started

    def _close_stage(self):
        elapsed = time.monotonic() - self._stage_started
        if self._stage_name and self._stage_name != "start":
            self.timings.append(
                {
                    "Stage": self._stage_name,
                    "Method": self._stage_method,
                    "Period": self.current_period,
                    "Seconds": round(elapsed, 3),
                }
            )

    def emit(self, stage, method=None, detail="", completed=None):
        if stage != self._stage_name or method != self._stage_method:
            self._close_stage()
            self._stage_started = time.monotonic()
            self._stage_name = stage
            self._stage_method = method
        if completed is not None:
            self.current_period = completed
        event = {
            "completed": self.current_period,
            "total": self.total_periods,
            "stage": stage,
            "method": method,
            "detail": detail,
            "elapsed_seconds": self._elapsed(),
            "period_elapsed_seconds": self._period_elapsed(),
            "period_start": None if self.period_start is None else str(self.period_start.date()),
            "period_end": None if self.period_end is None else str(self.period_end.date()),
        }
        _notify_progress(self.callback, event)

    def begin_period(self, number, period_start, period_end):
        self._close_stage()
        self.current_period = number
        self.period_started = time.monotonic()
        self.period_start = pd.Timestamp(period_start)
        self.period_end = pd.Timestamp(period_end)
        self._stage_started = self.period_started
        self._stage_name = "period"
        self._stage_method = None
        self.emit("period", detail=f"starting period {number}/{self.total_periods}")

    def finish(self):
        self.emit("complete", completed=self.total_periods, detail="benchmark complete")


def _merge_coverage_reports(*reports):
    failures = []
    unavailable = []
    seen_failure_keys = set()
    downloaded = []
    secondary = []
    classifications = {}
    statuses = []
    cache_dir = None
    for report in reports:
        if not report:
            continue
        statuses.append(report.get("Status"))
        cache_dir = report.get("Cache Directory") or cache_dir
        downloaded.extend(report.get("Downloaded Tickers") or [])
        secondary.extend(report.get("Secondary Sources Used") or [])
        classifications.update(report.get("Ticker Classifications") or {})
        unavailable.extend(report.get("Unavailable Valid Constituents") or [])
        for failure in report.get("Data Source Failures") or []:
            key = (
                failure.get("Ticker"),
                failure.get("Requested Start"),
                failure.get("Requested End"),
                failure.get("Provider"),
            )
            if key in seen_failure_keys:
                continue
            seen_failure_keys.add(key)
            failures.append(failure)
    return {
        "Status": "DOWNLOADING" if "DOWNLOADING" in statuses else "USING CACHE",
        "Downloaded Tickers": downloaded,
        "Cache Directory": cache_dir,
        "Data Source Failures": failures,
        "Unavailable Valid Constituents": sorted(set(unavailable)),
        "Secondary Sources Used": secondary,
        "Ticker Classifications": classifications,
        "Coverage Is Valid": not failures,
    }


def _overall_required_history_days():
    strategy_history = max(
        (get_strategy(name).required_history_days for name in available_strategy_names()),
        default=0,
    )
    return max(strategy_history, ML_REQUIRED_HISTORY_DAYS, REGIME_REQUIRED_HISTORY_DAYS)


def _extend_price_history(
    downloaded_data, tickers, price_history_start_date, evaluation_start_date, status_callback=None
):
    """Fetch additional PRICE-ONLY rows before `evaluation_start_date`, for
    ML training warmup, and merge them into `downloaded_data`.

    This deliberately calls `data.market_data.load_market_data` directly
    rather than anything that resolves Nasdaq-100 membership
    (`get_backtest_tickers`/`get_historical_universe`): fetching raw price
    rows for a ticker is safe for any date real market data exists for,
    even before `HISTORICAL_UNIVERSE_START_DATE` - it is universe
    *membership* lookups that are unsupported that far back, not prices.
    """
    if price_history_start_date >= evaluation_start_date:
        return downloaded_data, {
            "Status": "USING CACHE",
            "Downloaded Tickers": [],
            "Cache Directory": None,
            "Data Source Failures": [],
            "Unavailable Valid Constituents": [],
            "Secondary Sources Used": [],
            "Ticker Classifications": {},
        }
    extra_data, report = load_market_data(
        tickers,
        price_history_start_date,
        evaluation_start_date - pd.Timedelta(days=1),
        status_callback=status_callback,
        # Warmup rows are price-only. Do not treat holes outside membership
        # windows as required coverage failures; Yahoo misses are still
        # range-cached inside load_market_data so the same range is not retried.
        required_ranges={},
    )
    for ticker, extra_frame in extra_data.items():
        if extra_frame is None or extra_frame.empty:
            continue
        existing_frame = downloaded_data.get(ticker)
        if existing_frame is None or existing_frame.empty:
            downloaded_data[ticker] = extra_frame
            continue
        combined = pd.concat([extra_frame, existing_frame])
        combined = combined[~combined.index.duplicated(keep="last")].sort_index()
        downloaded_data[ticker] = combined
    return downloaded_data, report


def run_benchmark(
    duration,
    earliest_allowed,
    latest_allowed,
    number_of_tests,
    random_seed,
    requested_strategies=REQUESTED_RULE_BASED_STRATEGIES,
    include_regime_switching=True,
    ml_models=DEFAULT_ML_MODELS,
    top_n_values=DEFAULT_TOP_N_VALUES,
    benchmark=BACKTEST_BENCHMARK,
    starting_cash=BACKTEST_STARTING_CASH,
    fee_rate=BACKTEST_FEE_RATE,
    min_stock_price=MIN_STOCK_PRICE,
    max_position_value=MAX_POSITION_VALUE,
    position_limit_mode=POSITION_LIMIT_MODE,
    universe=None,
    price_data=None,
    progress_callback=None,
    training_window_days=TRAINING_WINDOW_CALENDAR_DAYS,
):
    """Run every requested method on the SAME set of historical periods.

    `price_data`, if given, overrides the network download entirely (used
    by tests, and by anyone re-using an already-downloaded dataset). All
    methods read from this exact same dict - the ML models additionally
    truncate it via `ml.features.truncate_to_as_of` before every
    prediction, and are trained only on the slice strictly before each
    period's start date (see `benchmarking.ml_training`).
    """
    requested_start_date = pd.Timestamp(earliest_allowed).normalize()
    requested_end_date = pd.Timestamp(latest_allowed).normalize()

    # Rule-based strategies (via `run_portfolio_simulation`) and ML training
    # (via `ml.dataset.build_feature_dataset`) both resolve point-in-time
    # Nasdaq-100 membership for every OBSERVATION date whenever `universe`
    # is not pinned to an explicit list. Reject an unsupported evaluation
    # range immediately, with a clear message, instead of letting a
    # membership lookup fail deep inside a period or ML fold.
    if universe is None and requested_start_date < HISTORICAL_UNIVERSE_START_DATE:
        raise ValueError(
            f"Requested benchmark start date {requested_start_date.date()} is before "
            f"{HISTORICAL_UNIVERSE_START_DATE.date()}, the earliest date historical "
            "Nasdaq-100 point-in-time membership is supported. Choose a start date on or "
            f"after {HISTORICAL_UNIVERSE_START_DATE.date()}, or pass an explicit `universe` "
            "list to bypass point-in-time membership."
        )

    periods = generate_random_periods(
        duration, requested_start_date, requested_end_date, number_of_tests, random_seed
    )

    availability = describe_availability(requested_strategies)
    available_names = [name for name, is_available in availability if is_available]
    unavailable_strategies = [name for name, is_available in availability if not is_available]

    # `evaluation_start_date`/`evaluation_end_date` are the actual
    # universe-membership / portfolio-simulation OBSERVATION dates: every
    # method is run on exactly this same window (Part 8, same-period
    # fairness). Do not confuse these with `price_history_start_date` below,
    # a PRICE-only warmup boundary for ML training that may legitimately
    # precede `HISTORICAL_UNIVERSE_START_DATE`.
    evaluation_start_date = min(start for start, _ in periods)
    evaluation_end_date = max(end for _, end in periods)

    overall_required_history_days = _overall_required_history_days()

    # Training OBSERVATION dates may never precede supported universe
    # history (when point-in-time membership is in effect). If the nominal
    # ~3-year training window would reach further back than that, it is
    # transparently shifted forward to the boundary rather than silently
    # producing membership lookups that would fail (Part 4).
    earliest_supported_training_start = HISTORICAL_UNIVERSE_START_DATE if universe is None else None
    nominal_training_start = evaluation_start_date - pd.Timedelta(days=training_window_days)
    effective_training_start_floor = (
        max(nominal_training_start, earliest_supported_training_start)
        if earliest_supported_training_start is not None
        else nominal_training_start
    )
    # Price history (not membership) must reach far enough before even the
    # earliest possible training_start to cover its own feature warmup
    # (MA200, etc.) - this is a bounded, fixed amount, independent of how
    # large `training_window_days` is.
    price_history_start_date = effective_training_start_floor - pd.Timedelta(
        days=overall_required_history_days * 3
    )

    progress = _StageTracker(progress_callback, len(periods))

    def _download_status(status, ticker):
        progress.emit("download", detail=f"{status} {ticker}")

    if price_data is None:
        # Universe membership/backtest-ticker discovery is evaluated only
        # across the real evaluation window - never across the deeper
        # price-warmup window ML training needs. Conflating these two was
        # the root cause of "membership unavailable before 2021-12-20"
        # firing for evaluation starts as late as 2023.
        progress.emit("download", detail="loading evaluation-window prices")
        downloaded_data, backtest_report = download_backtest_data(
            evaluation_start_date,
            evaluation_end_date,
            benchmark,
            overall_required_history_days,
            status_callback=_download_status,
        )
        # Separately extend PRICE history (no membership lookups involved at
        # all - see `_extend_price_history`) back to `price_history_start_date`
        # for ML training warmup, and make sure SPY/QQQ are always present:
        # regime detection and relative-strength features need both, but
        # only `benchmark` (SPY by default) is guaranteed to be fetched by
        # `download_backtest_data` above (QQQ is not itself a Nasdaq-100
        # constituent, so it is otherwise never downloaded).
        extension_tickers = sorted(set(downloaded_data) | {"SPY", "QQQ", benchmark})
        progress.emit("download", detail="extending training-warmup prices")
        downloaded_data, extend_report = _extend_price_history(
            downloaded_data,
            extension_tickers,
            price_history_start_date,
            evaluation_start_date,
            status_callback=_download_status,
        )
        data_coverage = _merge_coverage_reports(backtest_report, extend_report)
    else:
        downloaded_data = price_data
        data_coverage = _merge_coverage_reports()

    data_coverage = _merge_coverage_reports(
        data_coverage,
        audit_price_coverage(
            downloaded_data,
            effective_training_start_floor if ml_models else evaluation_start_date,
            evaluation_end_date,
            universe=universe, benchmark=benchmark,
        ),
    )
    reset_feature_cache()
    data_coverage["Coverage Diagnosis"] = diagnose_coverage_failures(
        data_coverage.get("Data Source Failures") or [],
        evaluation_start_date,
        evaluation_end_date,
        downloaded_data,
    )
    coverage_is_valid = bool(data_coverage.get("Coverage Is Valid", True))

    benchmark_price_data = {
        "SPY": get_ticker_data(downloaded_data, "SPY"),
        "QQQ": get_ticker_data(downloaded_data, "QQQ"),
    }

    errors = []
    period_rows = []
    trained_folds_by_model = {name: [] for name in ml_models}
    probability_log_by_model = {name: [] for name in ml_models}

    def _simulate(strategy_obj, period_start, period_end, method_name, top_n=None):
        def _simulation_progress(completed, total, date):
            progress.emit(
                "simulation",
                method=method_name,
                detail=f"{date.date()} ({completed}/{total} rebalances)",
            )

        return run_portfolio_simulation(
            strategy_obj,
            period_start,
            period_end,
            downloaded_data,
            benchmark=benchmark,
            starting_cash=starting_cash,
            fee_rate=fee_rate,
            min_stock_price=min_stock_price,
            max_position_value=max_position_value,
            position_limit_mode=position_limit_mode,
            top_n=top_n,
            method_name=method_name,
            universe=universe,
            progress_callback=_simulation_progress,
        )

    def _feature_progress(completed, total, date):
        progress.emit(
            "feature construction",
            detail=f"{date.date()} ({completed}/{total} weeks)",
        )

    def _audit_progress(detail):
        progress.emit("leakage audit", detail=detail)

    for test_number, (period_start, period_end) in enumerate(periods, start=1):
        progress.begin_period(test_number, period_start, period_end)

        for strategy_name in available_names:
            progress.emit("simulation", method=strategy_name)
            try:
                result = _simulate(get_strategy(strategy_name), period_start, period_end, strategy_name)
                period_rows.append(build_period_row(result, test_number))
            except Exception as error:  # noqa: BLE001 - one method's failure must not sink the run
                errors.append(f"{strategy_name} / test {test_number}: {error}")

        if include_regime_switching:
            progress.emit("simulation", method=REGIME_SWITCHING_METHOD_NAME)
            try:
                regime_strategy = RegimeSwitchingStrategy(benchmark_price_data)
                result = _simulate(regime_strategy, period_start, period_end, REGIME_SWITCHING_METHOD_NAME)
                period_rows.append(build_period_row(result, test_number))
            except Exception as error:  # noqa: BLE001
                errors.append(f"{REGIME_SWITCHING_METHOD_NAME} / test {test_number}: {error}")

        prepared_fold = None
        if ml_models:
            progress.emit("feature construction", detail="training fold")
            try:
                prepared_fold = prepare_training_fold(
                    period_start,
                    downloaded_data,
                    universe=universe,
                    min_supported_date=earliest_supported_training_start,
                    training_window_days=training_window_days,
                    progress_callback=_feature_progress,
                )
            except InsufficientTrainingDataError as error:
                for model_name in ml_models:
                    errors.append(f"{model_name} / test {test_number}: {error}")

        for model_name in ml_models:
            if prepared_fold is None:
                continue
            progress.emit("fitting", method=model_name)
            try:
                trained_fold = fit_prepared_fold(model_name, prepared_fold)
            except Exception as error:  # noqa: BLE001
                errors.append(f"{model_name} / test {test_number}: {error}")
                continue

            ml_strategy = MLRankingStrategy(
                name=model_name,
                pipeline=trained_fold.pipeline,
                feature_columns=trained_fold.feature_columns,
                categorical_columns=trained_fold.categorical_columns,
                benchmark_data=benchmark_price_data,
            )

            for top_n in top_n_values:
                method_label = f"{model_name} Top {top_n}"
                progress.emit("simulation", method=method_label)
                try:
                    result = _simulate(ml_strategy, period_start, period_end, method_label, top_n=top_n)
                    period_rows.append(build_period_row(result, test_number))
                except Exception as error:  # noqa: BLE001
                    errors.append(f"{method_label} / test {test_number}: {error}")

            trained_fold = replace(trained_fold, validation_rows=len(ml_strategy.probability_log))
            trained_folds_by_model[model_name].append(trained_fold)
            probability_log_by_model[model_name].extend(ml_strategy.probability_log)

    period_table = build_period_results_table(period_rows)
    aggregate_table = build_aggregate_table(period_table)

    all_trained_folds = [fold for folds in trained_folds_by_model.values() for fold in folds]
    fold_metrics_table = build_fold_metrics_table(all_trained_folds)

    feature_stability_rows = []
    feature_stability_details = []
    for model_name, folds in trained_folds_by_model.items():
        if not folds:
            continue
        summary, detail = compute_feature_stability(model_name, folds)
        summary.update(compute_prediction_distribution(probability_log_by_model[model_name]))
        feature_stability_rows.append(summary)
        feature_stability_details.append(detail)
    feature_stability_summary = pd.DataFrame(feature_stability_rows)
    feature_stability_table = (
        pd.concat(feature_stability_details, ignore_index=True)
        if feature_stability_details
        else pd.DataFrame()
    )

    sample_universe = universe or get_historical_universe(evaluation_end_date).tickers
    sample_feature_columns = all_trained_folds[0].feature_columns if all_trained_folds else []
    progress.emit("leakage audit", detail="running automated leakage checks")
    if not ml_models:
        leakage_audit = {
            "Checks": [
                {
                    "Check": "ML leakage audit",
                    "Passed": True,
                    "Detail": "No ML models requested; ML leakage checks are not applicable.",
                }
            ],
            "Is Valid": True,
        }
    else:
        leakage_audit = run_leakage_audit(
            all_trained_folds,
            downloaded_data,
            list(sample_universe)[:LEAKAGE_AUDIT_SAMPLE_UNIVERSE_SIZE],
            sample_feature_columns,
            progress_callback=_audit_progress,
            dataset_universe=universe,
        )

    expected_methods = set(requested_strategies)
    if include_regime_switching:
        expected_methods.add(REGIME_SWITCHING_METHOD_NAME)
    expected_methods.update(f"{model} Top {n}" for model in ml_models for n in top_n_values)
    expected_pairs = {(method, number) for method in expected_methods for number in range(1, len(periods) + 1)}
    actual_pairs = set(zip(period_table["Method"], period_table["Test"]))
    complete = bool(expected_pairs) and actual_pairs == expected_pairs and len(period_table) == len(expected_pairs)
    leakage_audit["Checks"].append({
        "Check": "Every requested method completed identical evaluation periods",
        "Passed": complete,
        "Detail": "All method/period pairs completed" if complete else f"Missing method/period pairs: {sorted(expected_pairs - actual_pairs)}",
    })
    leakage_audit["Is Valid"] = bool(leakage_audit["Is Valid"] and complete)

    existing_method_names = list(available_names) + (
        [REGIME_SWITCHING_METHOD_NAME] if include_regime_switching else []
    )
    existing_rows = (
        aggregate_table[aggregate_table["Method"].isin(existing_method_names)]
        if not aggregate_table.empty
        else aggregate_table
    )
    if not existing_rows.empty:
        sortable = existing_rows.copy()
        sortable["_sharpe_for_sort"] = sortable["Sharpe Ratio"].fillna(float("-inf"))
        best_existing_row = sortable.sort_values(
            ["Median Excess Return", "_sharpe_for_sort"], ascending=False
        ).iloc[0]
        best_existing_name = best_existing_row["Method"]
        best_existing_metrics = best_existing_row.to_dict()
    else:
        best_existing_name = "N/A"
        best_existing_metrics = {}

    promotion_results = []
    for model_name in ml_models:
        for top_n in top_n_values:
            method_label = f"{model_name} Top {top_n}"
            rows = (
                aggregate_table[aggregate_table["Method"] == method_label]
                if not aggregate_table.empty
                else aggregate_table
            )
            if rows.empty:
                continue
            ml_metrics = rows.iloc[0].to_dict()
            ml_period_returns = period_table.loc[
                period_table["Method"] == method_label, "Total Return"
            ].tolist()
            promotion_results.append(
                evaluate_promotion(
                    method_label,
                    ml_metrics,
                    best_existing_metrics,
                    best_existing_name,
                    ml_period_returns,
                    leakage_audit_is_valid=leakage_audit["Is Valid"],
                    coverage_is_valid=coverage_is_valid,
                )
            )
    promotion_table = build_promotion_table(promotion_results)

    ml_fold_summary = {
        model_name: {
            "Periods Tested": len(periods),
            "Valid Folds": len(trained_folds_by_model.get(model_name, [])),
        }
        for model_name in ml_models
    }

    config = {
        "duration": duration,
        "earliest_allowed": str(requested_start_date.date()),
        "latest_allowed": str(requested_end_date.date()),
        "number_of_tests": number_of_tests,
        "random_seed": random_seed,
        "requested_strategies": list(requested_strategies),
        "unavailable_strategies": unavailable_strategies,
        "include_regime_switching": include_regime_switching,
        "ml_models": list(ml_models),
        "top_n_values": list(top_n_values),
        "benchmark": benchmark,
        "starting_cash": starting_cash,
        "fee_rate": fee_rate,
        "min_stock_price": min_stock_price,
        "max_position_value": max_position_value,
        "position_limit_mode": position_limit_mode,
        "training_window_days": training_window_days,
        # Effective date boundaries actually used, for UI transparency
        # (Part 4/14): the requested range may differ from the evaluation
        # range (periods are sampled within it), and the training window is
        # transparently floored at `HISTORICAL_UNIVERSE_START_DATE` rather
        # than silently failing or fabricating earlier membership.
        "historical_universe_start_date": str(HISTORICAL_UNIVERSE_START_DATE.date()),
        "evaluation_start_date": str(evaluation_start_date.date()),
        "evaluation_end_date": str(evaluation_end_date.date()),
        "effective_training_start_floor": str(effective_training_start_floor.date()),
        "ml_fold_summary": ml_fold_summary,
        "leakage_audit_is_valid": leakage_audit["Is Valid"],
        "coverage_is_valid": coverage_is_valid,
        "benchmark_valid": leakage_audit["Is Valid"] and coverage_is_valid,
        "unavailable_valid_constituents": data_coverage.get("Unavailable Valid Constituents", []),
        "data_source_failure_count": len(data_coverage.get("Data Source Failures") or []),
        "stage_timings": progress.timings,
    }

    progress.finish()

    return BenchmarkResult(
        periods=periods,
        period_table=period_table,
        aggregate_table=aggregate_table,
        fold_metrics_table=fold_metrics_table,
        feature_stability_table=feature_stability_table,
        feature_stability_summary=feature_stability_summary,
        leakage_audit=leakage_audit,
        promotion_table=promotion_table,
        promotion_results=promotion_results,
        unavailable_strategies=unavailable_strategies,
        errors=errors,
        data_coverage=data_coverage,
        config=config,
    )
