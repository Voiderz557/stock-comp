"""Walk-forward evaluation: classification metrics + trading-relevant metrics.

`run_walk_forward_evaluation` fits one fresh model per fold (never reusing a
model across folds, and never letting a fold's validation data influence any
other fold's training), then reports both:

- standard classification metrics (accuracy/precision/recall/F1/ROC AUC)
- "trading-relevant" metrics: if you ranked stocks by predicted probability
  of beating the benchmark and bought the top N each rebalance date, what
  would have happened - compared against a random-N baseline and (if
  available) an existing strategy's own ranking.

Classification accuracy alone is never treated as evidence of profitability
- that is exactly why the top-N trading metrics exist alongside it.
"""

from __future__ import annotations

import warnings

import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score, f1_score, precision_score, recall_score, roc_auc_score

from ml.models import infer_feature_columns, predicted_positive_probability
from ml.validation import assert_no_temporal_leakage, split_dataset_by_fold, purge_unobserved_targets

DEFAULT_TOP_N_LIST = (5, 10, 20)
RANDOM_BASELINE_SEED = 42
DEFAULT_FORWARD_RETURN_COLUMN = "Forward Return 20D"
DEFAULT_BENCHMARK_RETURN_COLUMN = None
PREDICTED_PROBABILITY_COLUMN = "Predicted Probability"
DECISION_THRESHOLD = 0.5


def _classification_metrics(y_true, y_pred, y_proba):
    metrics = {
        "Accuracy": accuracy_score(y_true, y_pred),
        "Precision": precision_score(y_true, y_pred, zero_division=0),
        "Recall": recall_score(y_true, y_pred, zero_division=0),
        "F1": f1_score(y_true, y_pred, zero_division=0),
    }
    metrics["ROC AUC"] = float("nan")
    try:
        if len(set(y_true)) > 1 and y_proba is not None and np.isfinite(y_proba).all():
            metrics["ROC AUC"] = float(roc_auc_score(y_true, y_proba))
    except ValueError:
        metrics["ROC AUC"] = float("nan")
    return metrics


def _empty_top_n_result():
    return {
        "Average Forward Return": np.nan,
        "Average Excess Return": np.nan,
        "Hit Rate Vs Benchmark": np.nan,
        "Positive Return Rate": np.nan,
        "Dates Evaluated": 0,
    }


def _summarize_per_date_picks(per_date_records):
    if not per_date_records:
        return _empty_top_n_result()
    per_date_df = pd.DataFrame(per_date_records)
    return {
        "Average Forward Return": per_date_df["Average Forward Return"].mean(),
        "Average Excess Return": per_date_df.get(
            "Average Excess Return", pd.Series(dtype=float)
        ).mean(),
        "Hit Rate Vs Benchmark": per_date_df.get(
            "Hit Rate Vs Benchmark", pd.Series(dtype=float)
        ).mean(),
        "Positive Return Rate": per_date_df["Positive Return Rate"].mean(),
        "Dates Evaluated": len(per_date_df),
    }


def evaluate_top_n_picks(
    scored_df,
    n,
    date_column="Date",
    score_column=PREDICTED_PROBABILITY_COLUMN,
    forward_return_column=DEFAULT_FORWARD_RETURN_COLUMN,
    benchmark_return_column=DEFAULT_BENCHMARK_RETURN_COLUMN,
):
    """Per rebalance date, take the top-`n` rows by `score_column`.

    Averages the resulting forward return / excess return / hit rate /
    positive-return rate across every date present in `scored_df`, mirroring
    an actual weekly-rebalance "buy the top N" strategy.
    """
    per_date_records = []
    for date, group in scored_df.groupby(date_column):
        top = group.sort_values(score_column, ascending=False).head(n)
        returns = top[forward_return_column].dropna()
        if returns.empty:
            continue
        record = {
            "Date": date,
            "Average Forward Return": returns.mean(),
            "Positive Return Rate": (returns > 0).mean(),
        }
        if benchmark_return_column and benchmark_return_column in top.columns:
            paired = top[[forward_return_column, benchmark_return_column]].dropna()
            if not paired.empty:
                excess = paired[forward_return_column] - paired[benchmark_return_column]
                record["Average Excess Return"] = excess.mean()
                record["Hit Rate Vs Benchmark"] = (excess > 0).mean()
        per_date_records.append(record)
    return _summarize_per_date_picks(per_date_records)


def random_baseline_top_n_picks(
    validation_df,
    n,
    date_column="Date",
    forward_return_column=DEFAULT_FORWARD_RETURN_COLUMN,
    benchmark_return_column=DEFAULT_BENCHMARK_RETURN_COLUMN,
    random_seed=RANDOM_BASELINE_SEED,
):
    """Same metrics as `evaluate_top_n_picks`, but for a random N-stock pick.

    Deterministic given `random_seed`, so results are reproducible.
    """
    rng = np.random.RandomState(random_seed)
    per_date_records = []
    for date, group in validation_df.groupby(date_column):
        available = group.dropna(subset=[forward_return_column])
        if available.empty:
            continue
        sample_size = min(n, len(available))
        sampled = available.sample(n=sample_size, random_state=rng.randint(0, 2**31 - 1))
        record = {
            "Date": date,
            "Average Forward Return": sampled[forward_return_column].mean(),
            "Positive Return Rate": (sampled[forward_return_column] > 0).mean(),
        }
        if benchmark_return_column and benchmark_return_column in sampled.columns:
            paired = sampled[[forward_return_column, benchmark_return_column]].dropna()
            if not paired.empty:
                excess = paired[forward_return_column] - paired[benchmark_return_column]
                record["Average Excess Return"] = excess.mean()
                record["Hit Rate Vs Benchmark"] = (excess > 0).mean()
        per_date_records.append(record)
    return _summarize_per_date_picks(per_date_records)


def strategy_baseline_top_n_picks(
    validation_df,
    n,
    strategy_score_column,
    date_column="Date",
    forward_return_column=DEFAULT_FORWARD_RETURN_COLUMN,
    benchmark_return_column=DEFAULT_BENCHMARK_RETURN_COLUMN,
):
    """Top-N metrics using an existing strategy's own score as the ranking.

    Reuses the `Strategy Score: <name>` feature column already present in
    the dataset (see `ml.features.compute_strategy_features`) - no strategy
    logic is recomputed here. Returns `None` if that column is not present.
    """
    if strategy_score_column not in validation_df.columns:
        return None
    renamed = validation_df.rename(columns={strategy_score_column: "__strategy_score__"})
    return evaluate_top_n_picks(
        renamed,
        n,
        date_column=date_column,
        score_column="__strategy_score__",
        forward_return_column=forward_return_column,
        benchmark_return_column=benchmark_return_column,
    )


def run_walk_forward_evaluation(
    dataset,
    folds,
    model_builder,
    target_column,
    feature_columns=None,
    categorical_columns=None,
    forward_return_column=DEFAULT_FORWARD_RETURN_COLUMN,
    benchmark_return_column=DEFAULT_BENCHMARK_RETURN_COLUMN,
    top_n_list=DEFAULT_TOP_N_LIST,
    strategy_score_column=None,
    date_column="Date",
):
    """Fit + evaluate `model_builder` on every fold; return a list of fold results.

    A fresh pipeline is built and fit per fold using only that fold's
    training rows; `assert_no_temporal_leakage` is checked before every fit.
    Rows with a missing `target_column` are dropped from both sides (see
    `ml.dataset.get_supervised_subset` for the same rule applied dataset-wide).
    """
    if feature_columns is None:
        feature_columns, categorical_columns = infer_feature_columns(
            dataset, categorical_columns=categorical_columns or ("Regime",)
        )
    categorical_columns = list(categorical_columns or ())
    model_columns = list(feature_columns) + categorical_columns

    fold_results = []
    for fold in folds:
        train_df, validation_df = split_dataset_by_fold(dataset, fold, date_column=date_column)
        assert_no_temporal_leakage(train_df, validation_df, date_column=date_column)

        train_df = purge_unobserved_targets(train_df, target_column, fold.train_end)
        train_df = train_df.dropna(subset=[target_column])
        validation_df = validation_df.dropna(subset=[target_column])
        if train_df.empty or validation_df.empty:
            continue
        if train_df[target_column].astype(bool).nunique() < 2:
            continue

        pipeline = model_builder(feature_columns, categorical_columns)
        X_train = train_df[model_columns]
        y_train = train_df[target_column].astype(bool)
        pipeline.fit(X_train, y_train)

        X_val = validation_df[model_columns]
        y_val = validation_df[target_column].astype(bool)
        y_proba = predicted_positive_probability(pipeline, X_val)
        y_pred = y_proba >= DECISION_THRESHOLD

        classification_metrics = _classification_metrics(y_val, y_pred, y_proba)

        scored_validation = validation_df.copy()
        scored_validation[PREDICTED_PROBABILITY_COLUMN] = y_proba

        top_n_metrics = {
            n: evaluate_top_n_picks(
                scored_validation,
                n,
                date_column=date_column,
                forward_return_column=forward_return_column,
                benchmark_return_column=benchmark_return_column,
            )
            for n in top_n_list
        }
        random_baseline_metrics = {
            n: random_baseline_top_n_picks(
                scored_validation,
                n,
                date_column=date_column,
                forward_return_column=forward_return_column,
                benchmark_return_column=benchmark_return_column,
            )
            for n in top_n_list
        }
        strategy_baseline_metrics = None
        if strategy_score_column:
            strategy_baseline_metrics = {
                n: strategy_baseline_top_n_picks(
                    scored_validation,
                    n,
                    strategy_score_column,
                    date_column=date_column,
                    forward_return_column=forward_return_column,
                    benchmark_return_column=benchmark_return_column,
                )
                for n in top_n_list
            }

        fold_results.append(
            {
                "Fold": fold.fold_index,
                "Train Start": fold.train_start,
                "Train End": fold.train_end,
                "Validation Start": fold.validation_start,
                "Validation End": fold.validation_end,
                "Train Rows": len(train_df),
                "Validation Rows": len(validation_df),
                "Classification Metrics": classification_metrics,
                "Top N Metrics": top_n_metrics,
                "Random Baseline Top N Metrics": random_baseline_metrics,
                "Strategy Baseline Top N Metrics": strategy_baseline_metrics,
                "Pipeline": pipeline,
            }
        )
    return fold_results


def _safe_nanmean(values):
    """`np.nanmean` that returns NaN (rather than warning) for all-NaN input."""
    values = [value for value in values if value is not None]
    if not values or all(pd.isna(value) for value in values):
        return float("nan")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        return float(np.nanmean(values))


def aggregate_fold_metrics(fold_results):
    """Average classification + top-N metrics across every fold."""
    if not fold_results:
        return {}
    classification_keys = fold_results[0]["Classification Metrics"].keys()
    aggregate = {
        "Classification Metrics": {
            key: _safe_nanmean([fold["Classification Metrics"][key] for fold in fold_results])
            for key in classification_keys
        },
        "Top N Metrics": {},
        "Folds Evaluated": len(fold_results),
    }
    top_n_values = fold_results[0]["Top N Metrics"]
    for n in top_n_values:
        metric_keys = top_n_values[n].keys()
        aggregate["Top N Metrics"][n] = {
            key: _safe_nanmean([fold["Top N Metrics"][n][key] for fold in fold_results])
            for key in metric_keys
        }
    return aggregate
