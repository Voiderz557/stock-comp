"""Simple, deliberately basic sklearn model pipelines.

Only two models are provided, per the ML research plan: Logistic Regression
and a Random Forest Classifier, both targeting `beats_SPY_20D` by default.
No neural networks, gradient boosting, or other more complex models are
included here on purpose.

Both pipelines share the same missing-value/categorical-encoding contract
(`sklearn.pipeline.Pipeline` + `sklearn.compose.ColumnTransformer`) so they
can be swapped interchangeably by `ml.evaluation.run_walk_forward_evaluation`.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from ml.labels import label_column_names

DEFAULT_CATEGORICAL_FEATURE_COLUMNS = ("Regime",)
NON_FEATURE_COLUMNS = ("Ticker", "Date", "As Of Trading Date")

NUMERIC_IMPUTE_STRATEGY = "median"
CATEGORICAL_IMPUTE_STRATEGY = "most_frequent"

RANDOM_STATE = 42
LOGISTIC_REGRESSION_MAX_ITER = 1000
RANDOM_FOREST_N_ESTIMATORS = 300
RANDOM_FOREST_MAX_DEPTH = 6

LOGISTIC_REGRESSION_MODEL_NAME = "Logistic Regression"
RANDOM_FOREST_MODEL_NAME = "Random Forest"


def infer_feature_columns(
    dataset,
    categorical_columns=DEFAULT_CATEGORICAL_FEATURE_COLUMNS,
    extra_exclude=None,
):
    """Split `dataset`'s columns into (numeric_features, categorical_features).

    Excludes identifier columns (`Ticker`/`Date`/`As Of Trading Date`) and
    every known label column (see `ml.labels.label_column_names`) so labels
    are never accidentally fed to a model as a feature.
    """
    exclude = set(NON_FEATURE_COLUMNS) | set(label_column_names()) | set(extra_exclude or ())
    categorical_present = [
        column
        for column in categorical_columns
        if column in dataset.columns and column not in exclude
    ]
    numeric_columns = [
        column
        for column in dataset.columns
        if column not in exclude
        and column not in categorical_present
        and pd.api.types.is_numeric_dtype(dataset[column])
    ]
    return numeric_columns, categorical_present


def _numeric_transformer():
    return Pipeline(
        [
            ("impute", SimpleImputer(strategy=NUMERIC_IMPUTE_STRATEGY)),
            ("scale", StandardScaler()),
        ]
    )


def _categorical_transformer():
    return Pipeline(
        [
            ("impute", SimpleImputer(strategy=CATEGORICAL_IMPUTE_STRATEGY)),
            ("encode", OneHotEncoder(handle_unknown="ignore")),
        ]
    )


def build_logistic_regression_pipeline(numeric_columns, categorical_columns=()):
    """Impute + scale numeric features, impute + one-hot categoricals, then fit LR."""
    transformers = []
    if numeric_columns:
        transformers.append(("numeric", _numeric_transformer(), list(numeric_columns)))
    if categorical_columns:
        transformers.append(
            ("categorical", _categorical_transformer(), list(categorical_columns))
        )
    preprocessor = ColumnTransformer(transformers)
    return Pipeline(
        [
            ("preprocess", preprocessor),
            (
                "model",
                LogisticRegression(
                    max_iter=LOGISTIC_REGRESSION_MAX_ITER, random_state=RANDOM_STATE
                ),
            ),
        ]
    )


def build_random_forest_pipeline(numeric_columns, categorical_columns=()):
    """Impute numeric features (no scaling needed for trees), one-hot categoricals, fit RF."""
    transformers = []
    if numeric_columns:
        transformers.append(
            ("numeric", SimpleImputer(strategy=NUMERIC_IMPUTE_STRATEGY), list(numeric_columns))
        )
    if categorical_columns:
        transformers.append(
            ("categorical", _categorical_transformer(), list(categorical_columns))
        )
    preprocessor = ColumnTransformer(transformers)
    return Pipeline(
        [
            ("preprocess", preprocessor),
            (
                "model",
                RandomForestClassifier(
                    n_estimators=RANDOM_FOREST_N_ESTIMATORS,
                    max_depth=RANDOM_FOREST_MAX_DEPTH,
                    random_state=RANDOM_STATE,
                    n_jobs=-1,
                ),
            ),
        ]
    )


MODEL_BUILDERS = {
    LOGISTIC_REGRESSION_MODEL_NAME: build_logistic_regression_pipeline,
    RANDOM_FOREST_MODEL_NAME: build_random_forest_pipeline,
}


def predicted_positive_probability(pipeline, features):
    """Predicted probability of the positive (`True`) class for a fitted pipeline.

    Shared by `ml.evaluation` and `benchmarking.simulation` so both read
    predicted probabilities the same way, from the same fitted pipeline
    contract (a `Pipeline` whose final step is named `"model"`).
    """
    probabilities = pipeline.predict_proba(features)
    classes = list(pipeline.named_steps["model"].classes_)
    positive_index = classes.index(True) if True in classes else len(classes) - 1
    column = np.asarray(probabilities[:, positive_index], dtype=float)
    return np.where(np.isfinite(column), column, np.nan)


def get_feature_names_out(pipeline):
    """Expanded feature names (after one-hot encoding) from a fitted pipeline."""
    return list(pipeline.named_steps["preprocess"].get_feature_names_out())


def extract_feature_importance(pipeline, top_n=20):
    """Coefficients (Logistic Regression) or importances (Random Forest).

    Returns a `pandas.DataFrame` with columns `Feature` and either
    `Coefficient` or `Importance`, sorted by absolute magnitude descending
    and truncated to `top_n` rows.
    """
    model = pipeline.named_steps["model"]
    feature_names = get_feature_names_out(pipeline)
    if hasattr(model, "coef_"):
        values = model.coef_[0]
        value_column = "Coefficient"
    elif hasattr(model, "feature_importances_"):
        values = model.feature_importances_
        value_column = "Importance"
    else:
        raise ValueError("Model exposes neither coefficients nor feature importances.")

    table = pd.DataFrame({"Feature": feature_names, value_column: values})
    table["_abs"] = table[value_column].abs()
    table = table.sort_values("_abs", ascending=False).drop(columns="_abs")
    return table.head(top_n).reset_index(drop=True)
