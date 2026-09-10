import unittest

import numpy as np
import pandas as pd

from ml.models import (
    LOGISTIC_REGRESSION_MODEL_NAME,
    RANDOM_FOREST_MODEL_NAME,
    build_logistic_regression_pipeline,
    build_random_forest_pipeline,
    extract_feature_importance,
    infer_feature_columns,
)
from tests.ml_fixtures import build_sample_supervised_dataset

# Built once for the whole module - dataset construction from injected,
# in-memory price data is deterministic and reasonably fast, but repeating it
# per test would be wasteful.
_SUPERVISED_DATASET = build_sample_supervised_dataset()


class InferFeatureColumnsTests(unittest.TestCase):
    def test_identifier_and_label_columns_are_excluded(self):
        numeric_columns, categorical_columns = infer_feature_columns(_SUPERVISED_DATASET)
        for excluded in ("Ticker", "Date", "As Of Trading Date", "beats_SPY_20D", "Forward Return 20D"):
            self.assertNotIn(excluded, numeric_columns)
            self.assertNotIn(excluded, categorical_columns)

    def test_regime_is_categorical_not_numeric(self):
        numeric_columns, categorical_columns = infer_feature_columns(_SUPERVISED_DATASET)
        self.assertIn("Regime", categorical_columns)
        self.assertNotIn("Regime", numeric_columns)

    def test_known_numeric_features_are_present(self):
        numeric_columns, _ = infer_feature_columns(_SUPERVISED_DATASET)
        for expected in ("Price", "RSI14", "Momentum 20D", "Volatility 20D"):
            self.assertIn(expected, numeric_columns)


class PipelineTrainingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.numeric_columns, cls.categorical_columns = infer_feature_columns(_SUPERVISED_DATASET)
        cls.feature_columns = cls.numeric_columns + cls.categorical_columns
        cls.X = _SUPERVISED_DATASET[cls.feature_columns]
        cls.y = _SUPERVISED_DATASET["beats_SPY_20D"].astype(bool)

    def test_target_has_both_classes(self):
        self.assertEqual(self.y.nunique(), 2)

    def test_logistic_regression_trains_and_predicts_valid_probabilities(self):
        pipeline = build_logistic_regression_pipeline(self.numeric_columns, self.categorical_columns)
        pipeline.fit(self.X, self.y)
        probabilities = pipeline.predict_proba(self.X)
        self.assertEqual(probabilities.shape[0], len(self.X))
        self.assertTrue(np.all(probabilities >= 0.0))
        self.assertTrue(np.all(probabilities <= 1.0))
        self.assertTrue(np.allclose(probabilities.sum(axis=1), 1.0))

    def test_random_forest_trains_and_predicts_valid_probabilities(self):
        pipeline = build_random_forest_pipeline(self.numeric_columns, self.categorical_columns)
        pipeline.fit(self.X, self.y)
        probabilities = pipeline.predict_proba(self.X)
        self.assertEqual(probabilities.shape[0], len(self.X))
        self.assertTrue(np.all(probabilities >= 0.0))
        self.assertTrue(np.all(probabilities <= 1.0))

    def test_logistic_regression_is_deterministic(self):
        first = build_logistic_regression_pipeline(self.numeric_columns, self.categorical_columns)
        first.fit(self.X, self.y)
        second = build_logistic_regression_pipeline(self.numeric_columns, self.categorical_columns)
        second.fit(self.X, self.y)
        np.testing.assert_allclose(
            first.predict_proba(self.X), second.predict_proba(self.X)
        )

    def test_random_forest_handles_missing_values_via_imputation(self):
        X_with_gaps = self.X.copy()
        X_with_gaps.iloc[0, X_with_gaps.columns.get_loc("RSI14")] = np.nan
        pipeline = build_random_forest_pipeline(self.numeric_columns, self.categorical_columns)
        pipeline.fit(X_with_gaps, self.y)  # must not raise
        probabilities = pipeline.predict_proba(X_with_gaps)
        self.assertFalse(np.isnan(probabilities).any())

    def test_feature_importance_tables_have_expected_shape(self):
        logistic_pipeline = build_logistic_regression_pipeline(
            self.numeric_columns, self.categorical_columns
        )
        logistic_pipeline.fit(self.X, self.y)
        logistic_table = extract_feature_importance(logistic_pipeline, top_n=10)
        self.assertIn("Coefficient", logistic_table.columns)
        self.assertLessEqual(len(logistic_table), 10)

        forest_pipeline = build_random_forest_pipeline(self.numeric_columns, self.categorical_columns)
        forest_pipeline.fit(self.X, self.y)
        forest_table = extract_feature_importance(forest_pipeline, top_n=10)
        self.assertIn("Importance", forest_table.columns)
        self.assertLessEqual(len(forest_table), 10)


class ModelRegistryTests(unittest.TestCase):
    def test_expected_models_are_registered(self):
        from ml.models import MODEL_BUILDERS

        self.assertIn(LOGISTIC_REGRESSION_MODEL_NAME, MODEL_BUILDERS)
        self.assertIn(RANDOM_FOREST_MODEL_NAME, MODEL_BUILDERS)
        self.assertEqual(len(MODEL_BUILDERS), 2)  # deliberately just these two, per spec


if __name__ == "__main__":
    unittest.main()
