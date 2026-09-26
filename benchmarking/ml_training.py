"""Per-period, walk-forward-safe ML model training for the benchmark.

Each historical test period (see `backtesting.periods.generate_random_periods`)
is treated as one walk-forward fold: the model is trained ONLY on data
strictly before the period's start date, then applied - frozen, without
retraining - to rank stocks throughout the period. This module builds the
training dataset via the existing, unmodified `ml.dataset.build_feature_dataset`
and fits the model via the existing, unmodified `ml.models` pipelines - no
feature-engineering or model logic is duplicated here.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from ml.dataset import build_feature_dataset, get_supervised_subset
from ml.models import MODEL_BUILDERS, infer_feature_columns
from ml.validation import purge_unobserved_targets
from ml.labels import label_available_column

# ~3 years of weekly-sampled history to train on before every period. This is
# a readable, fixed constant - not tuned against any benchmark result.
TRAINING_WINDOW_CALENDAR_DAYS = 3 * 365
MIN_TRAINING_ROWS = 30
TRAINING_TARGET_COLUMN = "beats_SPY_20D"


class InsufficientTrainingDataError(RuntimeError):
    """Raised when a period does not have enough prior history to train on."""


@dataclass(frozen=True)
class TrainedMLFold:
    """Everything about one period's ML training, kept for stability/leakage auditing."""

    model_name: str
    period_start: pd.Timestamp
    training_start: pd.Timestamp
    training_end: pd.Timestamp
    pipeline: object
    feature_columns: list
    categorical_columns: list
    training_rows: int
    validation_rows: int
    class_balance: dict
    latest_training_label_at: pd.Timestamp | None = None


@dataclass(frozen=True)
class PreparedTrainingFold:
    """Walk-forward training rows shared across models for one period.

    Feature construction is identical for every model on the same fold.
    Each model still fits its own preprocessor and estimator on these rows.
    """

    period_start: pd.Timestamp
    training_start: pd.Timestamp
    training_end: pd.Timestamp
    supervised: pd.DataFrame
    feature_columns: list
    categorical_columns: list
    target_column: str


def build_training_window(
    period_start, min_supported_date=None, training_window_days=TRAINING_WINDOW_CALENDAR_DAYS
):
    """The `[training_start, training_end]` window strictly before `period_start`.

    `training_end = period_start - 1 day` guarantees training data never
    touches the period being evaluated, matching `ml.validation`'s
    `train_end < validation_start` walk-forward contract exactly.
    `training_window_days` is overridable (default `TRAINING_WINDOW_CALENDAR_DAYS`)
    so tests can use a much shorter window than the ~3 real years used by
    the benchmark UI by default.
    """
    period_start = pd.Timestamp(period_start).normalize()
    training_end = period_start - pd.Timedelta(days=1)
    training_start = training_end - pd.Timedelta(days=training_window_days)
    if min_supported_date is not None:
        training_start = max(training_start, pd.Timestamp(min_supported_date).normalize())
    return training_start, training_end


def prepare_training_fold(
    period_start,
    price_data,
    universe=None,
    min_supported_date=None,
    target_column=TRAINING_TARGET_COLUMN,
    include_strategy_features=True,
    training_window_days=TRAINING_WINDOW_CALENDAR_DAYS,
    progress_callback=None,
):
    """Build the labeled training table once for `[training_start, training_end]`.

    Call this once per period, then `fit_prepared_fold` for each model so
    feature construction is not repeated. Fitting/preprocessing stay inside
    each model's pipeline.
    """
    training_start, training_end = build_training_window(
        period_start, min_supported_date, training_window_days
    )
    if training_start >= training_end:
        raise InsufficientTrainingDataError(
            f"Training window is empty before {pd.Timestamp(period_start).date()}."
        )

    training_dataset = build_feature_dataset(
        training_start,
        training_end,
        universe=universe,
        rebalance_frequency="weekly",
        price_data={ticker: frame.loc[frame.index <= training_end] if frame is not None else None
                    for ticker, frame in price_data.items()},
        include_strategy_features=include_strategy_features,
        progress_callback=progress_callback,
    )
    if training_dataset.empty or target_column not in training_dataset.columns:
        raise InsufficientTrainingDataError(
            f"No labeled training rows before {pd.Timestamp(period_start).date()}."
        )
    supervised = get_supervised_subset(training_dataset, target_column)
    supervised = purge_unobserved_targets(supervised, target_column, training_end)
    if (
        len(supervised) < MIN_TRAINING_ROWS
        or supervised[target_column].astype(bool).nunique() < 2
    ):
        raise InsufficientTrainingDataError(
            f"Only {len(supervised)} usable training rows before "
            f"{pd.Timestamp(period_start).date()} (need >= {MIN_TRAINING_ROWS} "
            "rows with both classes present)."
        )

    feature_columns, categorical_columns = infer_feature_columns(supervised)
    return PreparedTrainingFold(
        period_start=pd.Timestamp(period_start),
        training_start=training_start,
        training_end=training_end,
        supervised=supervised,
        feature_columns=feature_columns,
        categorical_columns=categorical_columns,
        target_column=target_column,
    )


def fit_prepared_fold(model_name, prepared_fold):
    """Fit one model on an already-built training fold. Does not rebuild features."""
    if model_name not in MODEL_BUILDERS:
        raise ValueError(f"Unknown ML model '{model_name}'. Choose from {list(MODEL_BUILDERS)}.")

    pipeline = MODEL_BUILDERS[model_name](
        prepared_fold.feature_columns, prepared_fold.categorical_columns
    )
    X = prepared_fold.supervised[
        prepared_fold.feature_columns + prepared_fold.categorical_columns
    ]
    y = prepared_fold.supervised[prepared_fold.target_column].astype(bool)
    pipeline.fit(X, y)

    class_balance = {
        str(key): float(value) for key, value in y.value_counts(normalize=True).items()
    }

    return TrainedMLFold(
        model_name=model_name,
        period_start=prepared_fold.period_start,
        training_start=prepared_fold.training_start,
        training_end=prepared_fold.training_end,
        pipeline=pipeline,
        feature_columns=prepared_fold.feature_columns,
        categorical_columns=prepared_fold.categorical_columns,
        training_rows=len(prepared_fold.supervised),
        validation_rows=0,
        class_balance=class_balance,
        latest_training_label_at=pd.to_datetime(
            prepared_fold.supervised[label_available_column(prepared_fold.target_column)]
        ).max(),
    )


def train_ml_model_for_period(
    model_name,
    period_start,
    price_data,
    universe=None,
    min_supported_date=None,
    target_column=TRAINING_TARGET_COLUMN,
    include_strategy_features=True,
    training_window_days=TRAINING_WINDOW_CALENDAR_DAYS,
    progress_callback=None,
):
    """Train `model_name` using only data strictly before `period_start`.

    Returns a `TrainedMLFold`. Raises `InsufficientTrainingDataError` if
    there is not enough usable, class-balanced training data (e.g. the
    period is too close to the start of available history). Convenience
    wrapper around `prepare_training_fold` + `fit_prepared_fold`.
    """
    prepared = prepare_training_fold(
        period_start,
        price_data,
        universe=universe,
        min_supported_date=min_supported_date,
        target_column=target_column,
        include_strategy_features=include_strategy_features,
        training_window_days=training_window_days,
        progress_callback=progress_callback,
    )
    return fit_prepared_fold(model_name, prepared)
