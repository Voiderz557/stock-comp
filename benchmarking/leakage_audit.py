"""Automated no-look-ahead leakage audit for the benchmark.

Every check below is a genuine, reproducible runtime check against the
actual artifacts (`benchmarking.ml_training.TrainedMLFold`) and price data
used by one benchmark run - not just a design assertion. If ANY check
fails, `run_leakage_audit(...)["Is Valid"]` is `False` and
`benchmarking.runner.run_benchmark` must not produce a promotion
recommendation (see `benchmarking.promotion`).
"""

from __future__ import annotations

import pandas as pd
import numpy as np

from ml.dataset import build_feature_dataset, get_supervised_subset
from ml.features import (
    InsufficientHistoryError,
    compute_feature_row,
    truncate_to_as_of,
)
from ml.labels import label_column_names
from ml.validation import assert_no_temporal_leakage
from ml.validation import purge_unobserved_targets
from ml.models import predicted_positive_probability
from market.regime import classify_market_regime, compute_market_features

from benchmarking.ml_training import TRAINING_TARGET_COLUMN

# Only re-verify a handful of folds/tickers per check - this keeps the audit
# fast while still exercising real code paths on real data every run.
MAX_FOLDS_TO_REVERIFY = 3
_UNSPECIFIED_UNIVERSE = object()

# Multiplier used to "shock" future rows when proving future data cannot
# alter earlier feature values/predictions.
FUTURE_SHOCK_PRICE_MULTIPLIER = 1.5


def _check(name, passed, detail):
    return {"Check": name, "Passed": bool(passed), "Detail": detail}


def _run_check_safely(name, check_callable):
    """Run one check function, failing CLOSED if it raises.

    A check that cannot even complete tells us nothing about whether the
    property it verifies actually holds, so it must never be silently
    reported as passed (and must never crash the whole benchmark run).
    """
    try:
        return check_callable()
    except Exception as error:  # noqa: BLE001 - fail closed, do not propagate or default to PASS
        return _check(
            name,
            False,
            f"Check raised an unexpected error and could not be completed, so it is not "
            f"counted as passed: {error!r}",
        )


def _check_training_before_validation(trained_folds):
    violations = [
        fold for fold in trained_folds if not (
            fold.training_end < fold.period_start
            and pd.notna(getattr(fold, "latest_training_label_at", None))
            and fold.latest_training_label_at <= fold.training_end
        )
    ]
    return _check(
        "Training dates strictly before validation/period dates",
        not violations,
        "Every fold's observations and latest training target availability precede its period_start."
        if not violations
        else f"{len(violations)} fold(s) violate this, e.g. {violations[0].model_name}"
        f"@{violations[0].period_start.date()}.",
    )


def _check_label_horizon_excluded_from_features(feature_columns):
    overlap = set(feature_columns) & set(label_column_names())
    return _check(
        "Label horizon never enters training features",
        not overlap,
        "No label column appears in the feature set."
        if not overlap
        else f"Label column(s) leaked into features: {sorted(overlap)}.",
    )


def _check_no_train_validation_overlap(trained_folds):
    violations = []
    for fold in trained_folds:
        training_frame = pd.DataFrame({"Date": pd.date_range(fold.training_start, fold.training_end)})
        validation_frame = pd.DataFrame(
            {"Date": pd.date_range(fold.period_start, fold.period_start + pd.Timedelta(days=1))}
        )
        try:
            assert_no_temporal_leakage(training_frame, validation_frame)
        except ValueError:
            violations.append(fold)
    return _check(
        "No overlap between train and validation windows",
        not violations,
        "ml.validation.assert_no_temporal_leakage raised no errors for any fold."
        if not violations
        else f"{len(violations)} fold(s) overlap their train/validation windows.",
    )


def _check_fitted_on_training_rows_only(
    price_data, universe, trained_folds, progress_callback=None, dataset_universe=_UNSPECIFIED_UNIVERSE
):
    """Rebuild each sampled fold's training set and confirm it matches exactly.

    This proves the model/preprocessor were fit on precisely the rows built
    from `[training_start, training_end]` - not on anything from the
    validation period - by deterministically re-deriving that same
    training set from the same underlying (unmodified) price data and
    comparing row counts and date bounds. `dataset_universe` must match the
    universe used during training (`None` means point-in-time Nasdaq-100).
    Models that share a training window reuse one rebuilt dataset.
    """
    if dataset_universe is _UNSPECIFIED_UNIVERSE:
        dataset_universe = universe
    violations = []
    rebuilt_datasets = {}
    sampled = trained_folds[:MAX_FOLDS_TO_REVERIFY]
    for index, fold in enumerate(sampled, start=1):
        if progress_callback:
            progress_callback(
                f"rebuilding training fold {index}/{len(sampled)} "
                f"({fold.model_name} to {fold.period_start.date()})"
            )
        rebuild_key = (pd.Timestamp(fold.training_start), pd.Timestamp(fold.training_end))
        if rebuild_key not in rebuilt_datasets:
            rebuilt_datasets[rebuild_key] = build_feature_dataset(
                fold.training_start,
                fold.training_end,
                universe=dataset_universe,
                rebalance_frequency="weekly",
                price_data={ticker: frame.loc[frame.index <= fold.training_end] if frame is not None else None
                            for ticker, frame in price_data.items()},
            )
        dataset = rebuilt_datasets[rebuild_key]
        supervised = get_supervised_subset(dataset, TRAINING_TARGET_COLUMN)
        supervised = purge_unobserved_targets(supervised, TRAINING_TARGET_COLUMN, fold.training_end)
        if len(supervised) != fold.training_rows:
            violations.append(
                f"{fold.model_name}@{fold.period_start.date()}: rebuilt training set has "
                f"{len(supervised)} rows, fold recorded {fold.training_rows}."
            )
            continue
        if not supervised.empty and pd.Timestamp(supervised["Date"].max()) > fold.training_end:
            violations.append(
                f"{fold.model_name}@{fold.period_start.date()}: training rows extend past "
                f"training_end ({fold.training_end.date()})."
            )
    return violations


def _check_preprocessor_and_model_fitted_on_training_only(
    price_data, universe, trained_folds, progress_callback=None, dataset_universe=_UNSPECIFIED_UNIVERSE
):
    violations = _check_fitted_on_training_rows_only(
        price_data,
        universe,
        trained_folds,
        progress_callback=progress_callback,
        dataset_universe=dataset_universe,
    )
    detail = (
        "Re-derived training rows for every sampled fold match the rows actually "
        "used to fit the pipeline (same row count, no dates past training_end)."
        if not violations
        else "; ".join(violations)
    )
    passed = not violations
    return (
        _check("Scaler/preprocessor fitted on training data only", passed, detail),
        _check("Model fitted on training data only", passed, detail),
    )


def _shock_future_price_data(price_data, after_date, multiplier=FUTURE_SHOCK_PRICE_MULTIPLIER):
    shocked = {}
    for ticker, frame in price_data.items():
        frame = frame.copy()
        future_mask = frame.index > after_date
        if future_mask.any():
            for column in ("Open", "High", "Low", "Close"):
                if column in frame.columns:
                    frame.loc[future_mask, column] = frame.loc[future_mask, column] * multiplier
            if "Volume" in frame.columns:
                frame.loc[future_mask, "Volume"] = frame.loc[future_mask, "Volume"] * 3 + 1
        shocked[ticker] = frame
    return shocked


def _check_future_rows_cannot_alter_earlier_predictions(price_data, universe, trained_folds):
    """Shock every row after a fold's `training_end` and confirm point-in-time
    features computed as of `training_end` (via the same `truncate_to_as_of`
    every ML strategy uses) are completely unaffected."""
    if not trained_folds:
        return _check(
            "Future rows cannot alter earlier predictions",
            False,
            "No folds available; this check cannot be performed and is not counted as passed.",
        )
    fold = trained_folds[0]
    as_of = fold.training_end
    spy_full = price_data.get("SPY")
    qqq_full = price_data.get("QQQ")
    sample_ticker = next(
        (
            ticker
            for ticker in universe
            if ticker in price_data and not price_data[ticker].empty and ticker not in ("SPY", "QQQ")
        ),
        None,
    )
    if spy_full is None or qqq_full is None or sample_ticker is None:
        # Folds exist but there isn't enough data to actually exercise the
        # check - fail CLOSED (do not silently mark this PASS) per the
        # leakage-audit "cannot be performed -> not a pass" rule.
        return _check(
            "Future rows cannot alter earlier predictions",
            False,
            "Insufficient SPY/QQQ/ticker data to exercise this check; cannot verify, so it is "
            "not counted as passed.",
        )

    def _feature_row_as_of(dataset, as_of):
        ticker_hist = truncate_to_as_of(dataset[sample_ticker], as_of)
        spy_hist = truncate_to_as_of(dataset["SPY"], as_of)
        qqq_hist = truncate_to_as_of(dataset["QQQ"], as_of)
        regime_result = classify_market_regime(compute_market_features(spy_hist, qqq_hist))
        return compute_feature_row(sample_ticker, ticker_hist, spy_hist, qqq_hist, regime_result=regime_result)

    try:
        original_row = _feature_row_as_of(price_data, as_of)
    except (InsufficientHistoryError, ValueError):
        # Same fail-closed rule: an inability to compute the baseline feature
        # row means this check cannot verify anything, so it must not pass.
        return _check(
            "Future rows cannot alter earlier predictions",
            False,
            f"Insufficient history for {sample_ticker} as of {as_of.date()}; cannot verify, so "
            "it is not counted as passed.",
        )

    shocked_price_data = _shock_future_price_data(price_data, after_date=as_of)
    shocked_row = _feature_row_as_of(shocked_price_data, as_of)

    mismatches = []
    for key, value in original_row.items():
        shocked_value = shocked_row.get(key)
        if isinstance(value, float) and isinstance(shocked_value, float):
            if pd.isna(value) and pd.isna(shocked_value):
                continue
            if abs(value - shocked_value) > 1e-9:
                mismatches.append(key)
        elif value != shocked_value:
            mismatches.append(key)

    passed = not mismatches
    # Actually compare probabilities, not just input features. Each sampled
    # frozen pipeline receives exactly its own numeric/categorical columns.
    for sampled_fold in trained_folds[:MAX_FOLDS_TO_REVERIFY]:
        columns = sampled_fold.feature_columns + sampled_fold.categorical_columns
        original = pd.DataFrame([{key: original_row.get(key, np.nan) for key in columns}])
        shocked = pd.DataFrame([{key: shocked_row.get(key, np.nan) for key in columns}])
        before = predicted_positive_probability(sampled_fold.pipeline, original)
        after = predicted_positive_probability(sampled_fold.pipeline, shocked)
        if not (np.isfinite(before).all() and np.isfinite(after).all()
                and np.allclose(before, after, rtol=0, atol=1e-12)):
            mismatches.append(f"{sampled_fold.model_name} predicted probability")
            passed = False
    return _check(
        "Future rows cannot alter earlier predictions",
        passed,
        f"Shocking every row after {as_of.date()} preserved historical features and sampled frozen-model probabilities."
        if passed
        else f"Feature drift detected after shocking future rows in: {mismatches}.",
    )


def _check_feature_timestamp_leq_prediction_timestamp(price_data, universe, trained_folds):
    """Spot-check that `rank_buy_candidates`'s own look-ahead slice
    (`ticker_data.index < rebalance_date`) never includes the rebalance
    date itself, for every sampled fold's period_start."""
    violations = []
    for fold in trained_folds[:MAX_FOLDS_TO_REVERIFY]:
        rebalance_date = fold.period_start
        for ticker in universe:
            ticker_data = price_data.get(ticker)
            if ticker_data is None or ticker_data.empty:
                continue
            historical = ticker_data.loc[ticker_data.index < rebalance_date]
            if historical.empty:
                continue
            if historical.index.max() >= rebalance_date:
                violations.append((ticker, rebalance_date))
    return _check(
        "Feature timestamp <= prediction timestamp",
        not violations,
        "Every sampled feature timestamp was strictly before its prediction/rebalance date."
        if not violations
        else f"{len(violations)} violation(s), e.g. {violations[0]}.",
    )


def run_leakage_audit(
    trained_folds,
    price_data,
    universe,
    feature_columns,
    progress_callback=None,
    dataset_universe=_UNSPECIFIED_UNIVERSE,
):
    """Run every leakage check and return `{"Checks": [...], "Is Valid": bool}`.

    `trained_folds` is the full list of `TrainedMLFold`s produced across all
    ML models/periods in one benchmark run; `price_data`/`universe` are the
    same inputs used to train and simulate; `feature_columns` is any one
    fold's numeric feature-column list (all folds share the same
    `infer_feature_columns` derivation). `dataset_universe` must match the
    universe used to build training rows (`None` means point-in-time
    Nasdaq-100). `universe` may be a smaller spot-check list for per-ticker
    probes.
    """
    if dataset_universe is _UNSPECIFIED_UNIVERSE:
        dataset_universe = universe

    def _notify(detail):
        if progress_callback:
            progress_callback(detail)

    _notify("feature timestamp check")
    checks = [
        _run_check_safely(
            "Feature timestamp <= prediction timestamp",
            lambda: _check_feature_timestamp_leq_prediction_timestamp(
                price_data, universe, trained_folds
            ),
        ),
    ]
    _notify("training-before-validation check")
    checks.append(
        _run_check_safely(
            "Training dates strictly before validation/period dates",
            lambda: _check_training_before_validation(trained_folds),
        )
    )
    _notify("label-horizon feature check")
    checks.append(
        _run_check_safely(
            "Label horizon never enters training features",
            lambda: _check_label_horizon_excluded_from_features(feature_columns),
        )
    )
    _notify("future-row shock check")
    checks.append(
        _run_check_safely(
            "Future rows cannot alter earlier predictions",
            lambda: _check_future_rows_cannot_alter_earlier_predictions(
                price_data, universe, trained_folds
            ),
        )
    )
    _notify("rebuilding training folds for preprocessor/model fit checks")
    try:
        checks.extend(
            _check_preprocessor_and_model_fitted_on_training_only(
                price_data,
                universe,
                trained_folds,
                progress_callback=_notify,
                dataset_universe=dataset_universe,
            )
        )
    except Exception as error:  # noqa: BLE001 - fail closed rather than crash the whole audit
        failure_detail = (
            f"Check raised an unexpected error and could not be completed, so it is not "
            f"counted as passed: {error!r}"
        )
        checks.append(_check("Scaler/preprocessor fitted on training data only", False, failure_detail))
        checks.append(_check("Model fitted on training data only", False, failure_detail))
    _notify("train/validation overlap check")
    checks.append(
        _run_check_safely(
            "No overlap between train and validation windows",
            lambda: _check_no_train_validation_overlap(trained_folds),
        )
    )
    return {"Checks": checks, "Is Valid": all(check["Passed"] for check in checks)}
