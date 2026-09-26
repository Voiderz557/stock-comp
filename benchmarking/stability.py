"""ML model stability: feature importance, prediction distribution, class balance.

Every function here operates on artifacts already produced while training
and running an ML method (`benchmarking.ml_training.TrainedMLFold` and
`benchmarking.simulation.MLRankingStrategy.probability_log`) - nothing here
retrains or re-simulates anything.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ml.models import extract_feature_importance

# Only compare the top-K most important features per fold when measuring
# stability - deep in the tail, small importance/coefficient noise is
# expected and not meaningful.
FEATURE_IMPORTANCE_TOP_N = 10

# If the average pairwise Jaccard similarity of each fold's top-N feature
# set falls below this, the model is flagged as having wildly changing
# feature importance across folds.
UNSTABLE_JACCARD_SIMILARITY_THRESHOLD = 0.4


def build_fold_metrics_table(trained_folds):
    """One row per ML training fold: sample sizes and class balance."""
    columns = [
        "Model",
        "Period Start",
        "Training Start",
        "Training End",
        "Training Rows",
        "Validation Rows",
        "Class Balance True %",
        "Class Balance False %",
    ]
    if not trained_folds:
        return pd.DataFrame(columns=columns)
    rows = [
        {
            "Model": fold.model_name,
            "Period Start": fold.period_start,
            "Training Start": fold.training_start,
            "Training End": fold.training_end,
            "Training Rows": fold.training_rows,
            "Validation Rows": fold.validation_rows,
            "Class Balance True %": fold.class_balance.get("True", np.nan),
            "Class Balance False %": fold.class_balance.get("False", np.nan),
        }
        for fold in trained_folds
    ]
    return pd.DataFrame(rows, columns=columns)


def compute_feature_stability(model_name, trained_folds, top_n=FEATURE_IMPORTANCE_TOP_N):
    """Feature-importance stability across a model's walk-forward folds.

    Returns `(summary_dict, detail_table)`. `detail_table` is long-format
    (Model, Period Start, Feature, value column) and is what gets exported
    to `feature_stability.csv`.
    """
    detail_rows = []
    top_feature_sets = []
    value_column = None

    for fold in trained_folds:
        table = extract_feature_importance(fold.pipeline, top_n=top_n)
        if table.empty:
            continue
        value_column = "Coefficient" if "Coefficient" in table.columns else "Importance"
        top_feature_sets.append(set(table["Feature"]))
        for _, row in table.iterrows():
            detail_rows.append(
                {
                    "Model": model_name,
                    "Period Start": fold.period_start,
                    "Feature": row["Feature"],
                    value_column: row[value_column],
                }
            )

    detail_table = pd.DataFrame(detail_rows)

    similarities = []
    for first, second in zip(top_feature_sets, top_feature_sets[1:]):
        union = first | second
        similarities.append(len(first & second) / len(union) if union else 1.0)
    average_similarity = float(np.mean(similarities)) if similarities else float("nan")
    unstable = bool(
        similarities and average_similarity < UNSTABLE_JACCARD_SIMILARITY_THRESHOLD
    )

    summary = {
        "Model": model_name,
        "Folds": len(trained_folds),
        "Value Column": value_column,
        "Average Top-Feature Jaccard Similarity": average_similarity,
        "Unstable Feature Importance": unstable,
    }
    return summary, detail_table


def compute_coefficient_sign_consistency(detail_table):
    """Logistic Regression: how consistently each feature's coefficient sign holds across folds."""
    columns = ["Feature", "Sign Consistency", "Folds Present", "Mean Coefficient", "Std Coefficient"]
    if detail_table.empty or "Coefficient" not in detail_table.columns:
        return pd.DataFrame(columns=columns)
    rows = []
    for feature, group in detail_table.groupby("Feature"):
        signs = np.sign(group["Coefficient"])
        mode_result = signs.mode()
        majority_sign = mode_result.iloc[0] if not mode_result.empty else 0
        rows.append(
            {
                "Feature": feature,
                "Sign Consistency": float((signs == majority_sign).mean()),
                "Folds Present": int(len(group)),
                "Mean Coefficient": float(group["Coefficient"].mean()),
                "Std Coefficient": float(group["Coefficient"].std(ddof=0)),
            }
        )
    return pd.DataFrame(rows, columns=columns).sort_values("Sign Consistency").reset_index(drop=True)


def compute_importance_rank_consistency(detail_table):
    """Random Forest: how consistently each feature's importance rank holds across folds."""
    columns = ["Feature", "Mean Rank", "Std Rank", "Folds Present"]
    if detail_table.empty or "Importance" not in detail_table.columns:
        return pd.DataFrame(columns=columns)
    ranked = detail_table.copy()
    ranked["Rank"] = ranked.groupby("Period Start")["Importance"].rank(
        ascending=False, method="first"
    )
    rows = []
    for feature, group in ranked.groupby("Feature"):
        rows.append(
            {
                "Feature": feature,
                "Mean Rank": float(group["Rank"].mean()),
                "Std Rank": float(group["Rank"].std(ddof=0)),
                "Folds Present": int(len(group)),
            }
        )
    return pd.DataFrame(rows, columns=columns).sort_values("Mean Rank").reset_index(drop=True)


def compute_prediction_distribution(probability_records):
    """Distribution of predicted probabilities issued across every fold/period."""
    if not probability_records:
        return {
            "Predictions": 0,
            "Min Probability": float("nan"),
            "Mean Probability": float("nan"),
            "Median Probability": float("nan"),
            "Max Probability": float("nan"),
            "Std Probability": float("nan"),
        }
    values = np.array([record["Probability"] for record in probability_records], dtype=float)
    return {
        "Predictions": int(len(values)),
        "Min Probability": float(values.min()),
        "Mean Probability": float(values.mean()),
        "Median Probability": float(np.median(values)),
        "Max Probability": float(values.max()),
        "Std Probability": float(values.std(ddof=0)),
    }
