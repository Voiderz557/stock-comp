"""Forward-return labels and classification targets.

LOOK-AHEAD NOTE
---------------
This is the ONLY module in the `ml` package allowed to read price rows dated
*after* the feature evaluation date `T`. That is intentional: labels
describe what happened *after* T, and are never fed back into
`ml.features`. Keeping that asymmetry confined to one small module makes it
easy to audit: nothing here ever mutates or returns a "feature", and nothing
in `ml.features` ever imports this module.

DEFINITIONS
-----------
For a feature row dated T and a horizon of H trading rows:

    forward_return_H = Close[T + H] / Close[T] - 1

where "T + H" means H trading rows after T's row in that ticker's own price
history (not H calendar days). If fewer than H future rows exist after T,
the forward return is NaN and the row should be excluded from any
supervised training that depends on it (see `ml.dataset.get_supervised_subset`).

Classification targets, all evaluated at `PRIMARY_LABEL_HORIZON_DAYS` (20
trading days) by default:

    beats_SPY_20D      = forward_return_20(ticker) > forward_return_20(SPY)
    beats_QQQ_20D      = forward_return_20(ticker) > forward_return_20(QQQ)
    positive_20D       = forward_return_20(ticker) > 0
    return_ge_5pct_20D = forward_return_20(ticker) >= 0.05

Any of these is NaN whenever the underlying forward return(s) it depends on
are NaN (insufficient future history), rather than silently defaulting to
False - this keeps "we don't know yet" distinct from "no" for quality
reporting and for excluding rows from training.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import re

FORWARD_RETURN_HORIZONS_DAYS = (5, 20, 60)
PRIMARY_LABEL_HORIZON_DAYS = 20
POSITIVE_RETURN_THRESHOLD = 0.0
RETURN_GE_THRESHOLD_PCT = 0.05
LABEL_BENCHMARKS = ("SPY", "QQQ")

# Fixed column names matching the literal spec wording, always computed at
# PRIMARY_LABEL_HORIZON_DAYS (20 trading days) regardless of what horizon a
# caller separately chooses to train a model on.
FIXED_CLASSIFICATION_COLUMNS = (
    "beats_SPY_20D",
    "beats_QQQ_20D",
    "positive_20D",
    "return_ge_5pct_20D",
)


def forward_return_column(horizon_days):
    return f"Forward Return {horizon_days}D"


def label_available_column(target):
    return f"Label Available At: {target}"


def label_end_date(closes, feature_date, horizon):
    if closes is None:
        return pd.NaT
    closes = closes.dropna()
    if not has_sufficient_future_history(closes, feature_date, horizon):
        return pd.NaT
    return closes.index[closes.index.get_loc(pd.Timestamp(feature_date)) + horizon]


def beats_benchmark_column(benchmark, horizon_days):
    return f"Beats {benchmark} {horizon_days}D"


def positive_column(horizon_days):
    return f"Positive {horizon_days}D"


def return_ge_column(threshold_pct, horizon_days):
    return f"Return Ge {int(round(threshold_pct * 100))}pct {horizon_days}D"


def label_column_names(
    horizons=FORWARD_RETURN_HORIZONS_DAYS,
    primary_horizon=PRIMARY_LABEL_HORIZON_DAYS,
    benchmarks=LABEL_BENCHMARKS,
):
    """Every label column `compute_labels` can produce, for exclusion lists.

    This is the single source of truth for label column naming, shared by
    `ml.dataset` (which builds the columns) and `ml.models`
    (which must exclude them from the feature set).
    """
    names = [forward_return_column(horizon) for horizon in horizons]
    if primary_horizon not in horizons:
        names.append(forward_return_column(primary_horizon))
    for benchmark in benchmarks:
        names.append(beats_benchmark_column(benchmark, primary_horizon))
    names.append(positive_column(primary_horizon))
    names.append(return_ge_column(RETURN_GE_THRESHOLD_PCT, primary_horizon))
    names.extend(FIXED_CLASSIFICATION_COLUMNS)
    return sorted(set(names + [label_available_column(name) for name in names]))


def has_sufficient_future_history(closes, feature_date, horizon_days):
    """Explicit, auditable check for "is there a valid H-row future close".

    Kept separate from `compute_forward_return` so dataset-quality reporting
    can distinguish "insufficient future data" from other NaN causes without
    duplicating the row-lookup logic.
    """
    closes = closes.dropna()
    feature_date = pd.Timestamp(feature_date)
    if feature_date not in closes.index:
        return False
    position = closes.index.get_loc(feature_date)
    return position + horizon_days < len(closes)


def compute_forward_return(closes, feature_date, horizon_days):
    """forward_return = Close[T + horizon] / Close[T] - 1.

    `T + horizon` is a positional offset of `horizon_days` TRADING ROWS
    after `feature_date` within `closes` - not a calendar offset. Returns
    NaN if `feature_date` is not present or there are not `horizon_days`
    rows of future data after it (see `has_sufficient_future_history`).
    """
    closes = closes.dropna()
    feature_date = pd.Timestamp(feature_date)
    if feature_date not in closes.index:
        return np.nan
    position = closes.index.get_loc(feature_date)
    target_position = position + horizon_days
    if target_position >= len(closes):
        return np.nan
    base_price = closes.iloc[position]
    future_price = closes.iloc[target_position]
    if pd.isna(base_price) or pd.isna(future_price) or base_price == 0:
        return np.nan
    return float(future_price / base_price - 1)


def _safe_gt(value, threshold):
    if pd.isna(value):
        return np.nan
    return bool(value > threshold)


def _safe_ge(value, threshold):
    if pd.isna(value):
        return np.nan
    return bool(value >= threshold)


def _safe_compare_gt(value, other):
    if pd.isna(value) or pd.isna(other):
        return np.nan
    return bool(value > other)


def compute_labels(
    ticker_closes,
    benchmark_closes,
    feature_date,
    horizons=FORWARD_RETURN_HORIZONS_DAYS,
    primary_horizon=PRIMARY_LABEL_HORIZON_DAYS,
):
    """Build every label column for one ticker/date row.

    `benchmark_closes` is a dict like `{"SPY": spy_close_series, "QQQ":
    qqq_close_series}`. `ticker_closes`/each benchmark series must be the
    FULL (not point-in-time-truncated) Close series for that symbol, since
    labels are explicitly allowed - and required - to look at future rows.
    """
    labels = {}
    for horizon in horizons:
        labels[forward_return_column(horizon)] = compute_forward_return(
            ticker_closes, feature_date, horizon
        )

    primary_key = forward_return_column(primary_horizon)
    if primary_key not in labels:
        labels[primary_key] = compute_forward_return(
            ticker_closes, feature_date, primary_horizon
        )
    ticker_primary_return = labels[primary_key]

    benchmark_primary_returns = {}
    for benchmark_name, benchmark_series in benchmark_closes.items():
        benchmark_return = compute_forward_return(
            benchmark_series, feature_date, primary_horizon
        )
        benchmark_primary_returns[benchmark_name] = benchmark_return
        labels[beats_benchmark_column(benchmark_name, primary_horizon)] = (
            _safe_compare_gt(ticker_primary_return, benchmark_return)
        )

    labels[positive_column(primary_horizon)] = _safe_gt(
        ticker_primary_return, POSITIVE_RETURN_THRESHOLD
    )
    labels[return_ge_column(RETURN_GE_THRESHOLD_PCT, primary_horizon)] = _safe_ge(
        ticker_primary_return, RETURN_GE_THRESHOLD_PCT
    )

    if primary_horizon == PRIMARY_LABEL_HORIZON_DAYS:
        labels["beats_SPY_20D"] = labels.get(
            beats_benchmark_column("SPY", primary_horizon), np.nan
        )
        labels["beats_QQQ_20D"] = labels.get(
            beats_benchmark_column("QQQ", primary_horizon), np.nan
        )
        labels["positive_20D"] = labels[positive_column(primary_horizon)]
        labels["return_ge_5pct_20D"] = labels[
            return_ge_column(RETURN_GE_THRESHOLD_PCT, primary_horizon)
        ]
    else:
        # Still compute the fixed 20D-named columns independently so they are
        # always present and comparable across datasets built with different
        # primary horizons.
        fixed_primary_return = compute_forward_return(
            ticker_closes, feature_date, PRIMARY_LABEL_HORIZON_DAYS
        )
        spy_series = benchmark_closes.get("SPY")
        qqq_series = benchmark_closes.get("QQQ")
        labels["beats_SPY_20D"] = _safe_compare_gt(
            fixed_primary_return,
            compute_forward_return(spy_series, feature_date, PRIMARY_LABEL_HORIZON_DAYS)
            if spy_series is not None
            else np.nan,
        )
        labels["beats_QQQ_20D"] = _safe_compare_gt(
            fixed_primary_return,
            compute_forward_return(qqq_series, feature_date, PRIMARY_LABEL_HORIZON_DAYS)
            if qqq_series is not None
            else np.nan,
        )
        labels["positive_20D"] = _safe_gt(fixed_primary_return, POSITIVE_RETURN_THRESHOLD)
        labels["return_ge_5pct_20D"] = _safe_ge(
            fixed_primary_return, RETURN_GE_THRESHOLD_PCT
        )

    # Record when each target becomes observable, separately from feature time.
    # Relative-return targets need BOTH the stock and benchmark future close.
    for name, value in list(labels.items()):
        horizon = int(re.search(r"(\d+)D$", name).group(1))
        available = label_end_date(ticker_closes, feature_date, horizon)
        benchmark = None
        if name.startswith("Beats "):
            benchmark = name.split()[1]
        elif name.startswith("beats_"):
            benchmark = name.split("_")[1]
        if benchmark is not None:
            other = label_end_date(benchmark_closes.get(benchmark), feature_date, horizon)
            available = max(available, other) if pd.notna(available) and pd.notna(other) else pd.NaT
        labels[label_available_column(name)] = available if pd.notna(value) else pd.NaT
    return labels
