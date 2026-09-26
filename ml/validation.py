"""Chronological walk-forward validation - no random train/test splits.

`generate_walk_forward_folds` produces a sequence of non-overlapping,
strictly time-ordered (train_end < validation_start) folds. `split_dataset_by_fold`
and `assert_no_temporal_leakage` are small, explicit, auditable helpers used
by `ml.evaluation` (and directly by tests) to prove that no row's date ever
appears on both sides of a fold.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
from ml.labels import label_available_column


def purge_unobserved_targets(dataset, target, known_by):
    """Keep only targets actually available by the training cutoff; fail closed.

    Legacy exported datasets without target timestamps must be regenerated.
    Observation-date ordering alone does not prevent forward-label leakage.
    """
    column = label_available_column(target)
    if column not in dataset:
        raise ValueError(f"Missing {column}; regenerate the dataset to verify label timing.")
    available = pd.to_datetime(dataset[column], errors="coerce")
    return dataset.loc[available.notna() & (available <= pd.Timestamp(known_by))].copy()

DEFAULT_MIN_TRAIN_DAYS = 365
DEFAULT_VALIDATION_DAYS = 90
DEFAULT_STEP_DAYS = 90

EXPANDING_WINDOW = "expanding"
ROLLING_WINDOW = "rolling"
WINDOW_MODES = (EXPANDING_WINDOW, ROLLING_WINDOW)


@dataclass(frozen=True)
class WalkForwardFold:
    """One chronological train/validation split.

    Both windows are inclusive calendar-date ranges. `train_end` is always
    exactly one day before `validation_start`, so there is never a gap or
    overlap between the two by construction.
    """

    fold_index: int
    train_start: pd.Timestamp
    train_end: pd.Timestamp
    validation_start: pd.Timestamp
    validation_end: pd.Timestamp


def generate_walk_forward_folds(
    start_date,
    end_date,
    min_train_days=DEFAULT_MIN_TRAIN_DAYS,
    validation_days=DEFAULT_VALIDATION_DAYS,
    step_days=DEFAULT_STEP_DAYS,
    window_mode=EXPANDING_WINDOW,
):
    """Build a list of `WalkForwardFold`s spanning `[start_date, end_date]`.

    window_mode="expanding" (default): `train_start` stays fixed at
    `start_date` and the training window grows by `step_days` every fold
    (e.g. Train 2022-01-01..2023-12-31, then 2022-01-01..2024-03-31, ...).

    window_mode="rolling": the training window keeps a fixed length of
    `min_train_days` and slides forward by `step_days` every fold, so old
    training data eventually ages out.

    Folds stop as soon as the next validation window would extend past
    `end_date`. Every fold satisfies `train_end < validation_start` and
    `validation_end <= end_date` by construction - this is what guarantees
    "training dates are always before validation dates" and "no leakage".
    """
    if window_mode not in WINDOW_MODES:
        raise ValueError(f"window_mode must be one of {WINDOW_MODES}.")
    if min_train_days <= 0 or validation_days <= 0 or step_days <= 0:
        raise ValueError(
            "min_train_days, validation_days, and step_days must all be positive."
        )

    start_date = pd.Timestamp(start_date).normalize()
    end_date = pd.Timestamp(end_date).normalize()
    if start_date >= end_date:
        raise ValueError("start_date must be before end_date.")

    folds = []
    fold_index = 0
    train_start = start_date
    train_end = start_date + pd.Timedelta(days=min_train_days - 1)

    while True:
        validation_start = train_end + pd.Timedelta(days=1)
        validation_end = validation_start + pd.Timedelta(days=validation_days - 1)
        if validation_end > end_date:
            break
        folds.append(
            WalkForwardFold(
                fold_index=fold_index,
                train_start=train_start,
                train_end=train_end,
                validation_start=validation_start,
                validation_end=validation_end,
            )
        )
        fold_index += 1
        if window_mode == EXPANDING_WINDOW:
            train_end = train_end + pd.Timedelta(days=step_days)
        else:
            train_start = train_start + pd.Timedelta(days=step_days)
            train_end = train_end + pd.Timedelta(days=step_days)

    return folds


def split_dataset_by_fold(dataset, fold, date_column="Date"):
    """Slice `dataset` into (train_df, validation_df) for one fold.

    Uses inclusive `[start, end]` comparisons against `fold`'s date ranges;
    since those ranges never overlap (by construction of the fold), the two
    returned frames can never share a date.
    """
    dates = pd.to_datetime(dataset[date_column])
    train_mask = (dates >= fold.train_start) & (dates <= fold.train_end)
    validation_mask = (dates >= fold.validation_start) & (dates <= fold.validation_end)
    return dataset.loc[train_mask].copy(), dataset.loc[validation_mask].copy()


def assert_no_temporal_leakage(train_df, validation_df, date_column="Date"):
    """Raise `ValueError` if training data could leak into validation.

    This is an explicit, easy-to-audit guard used both by tests and by
    `ml.evaluation.run_walk_forward_evaluation` before every fold is fit:
    it fails loudly the moment training data reaches into or past the
    validation window, or if any exact date is shared by both.
    """
    if train_df.empty or validation_df.empty:
        return
    train_dates = pd.to_datetime(train_df[date_column])
    validation_dates = pd.to_datetime(validation_df[date_column])
    if train_dates.max() >= validation_dates.min():
        raise ValueError(
            "Temporal leakage detected: training data extends into or past "
            "the validation window "
            f"(train max={train_dates.max()}, validation min={validation_dates.min()})."
        )
    overlap = set(train_dates) & set(validation_dates)
    if overlap:
        raise ValueError(
            f"Temporal leakage detected: {len(overlap)} date(s) appear in "
            "both training and validation."
        )
