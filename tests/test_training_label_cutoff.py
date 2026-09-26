"""Regression: earlier observation dates alone do not prevent target leakage."""
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import pandas as pd

from benchmarking.leakage_audit import _check_fitted_on_training_rows_only
from benchmarking.ml_training import prepare_training_fold, fit_prepared_fold
from ml.feature_cache import reset_feature_cache
from ml.labels import label_available_column
from ml.models import predicted_positive_probability
from ml.validation import purge_unobserved_targets
from tests.ml_fixtures import make_multi_asset_price_data


class TrainingLabelCutoffTests(unittest.TestCase):
    def test_future_prices_cannot_change_training_rows_targets_or_fitted_predictions(self):
        prices = make_multi_asset_price_data(n=650)
        period = prices["AAPL"].index[520]
        universe = ["AAPL", "MSFT", "NVDA", "AMZN"]
        reset_feature_cache()
        original = prepare_training_fold(period, prices, universe=universe, training_window_days=260)
        changed = {ticker: frame.copy() for ticker, frame in prices.items()}
        for frame in changed.values():
            frame.loc[frame.index >= period, ["Open", "Close"]] *= 10
        reset_feature_cache()
        shocked = prepare_training_fold(period, changed, universe=universe, training_window_days=260)
        pd.testing.assert_frame_equal(original.supervised, shocked.supervised)
        available = pd.to_datetime(original.supervised[label_available_column("beats_SPY_20D")])
        self.assertTrue((available < period).all())
        first = fit_prepared_fold("Logistic Regression", original)
        second = fit_prepared_fold("Logistic Regression", shocked)
        columns = original.feature_columns + original.categorical_columns
        a = predicted_positive_probability(first.pipeline, original.supervised[columns].head())
        b = predicted_positive_probability(second.pipeline, original.supervised[columns].head())
        self.assertEqual(a.tolist(), b.tolist())

    def test_research_split_purges_unavailable_labels(self):
        data = pd.DataFrame({"Date": pd.to_datetime(["2023-01-01", "2023-01-02"]),
            "beats_SPY_20D": [True, False],
            label_available_column("beats_SPY_20D"): pd.to_datetime(["2023-01-31", "2023-02-01"])})
        self.assertEqual(len(purge_unobserved_targets(data, "beats_SPY_20D", "2023-01-31")), 1)

    def test_old_dataset_without_label_time_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "regenerate"):
            purge_unobserved_targets(pd.DataFrame({"beats_SPY_20D": [True]}), "beats_SPY_20D", "2023-01-31")

    def test_explicit_point_in_time_universe_not_replaced_by_audit_sample(self):
        dataset = pd.DataFrame({"Date": [pd.Timestamp("2023-01-01")], "beats_SPY_20D": [True],
            label_available_column("beats_SPY_20D"): [pd.Timestamp("2023-01-30")]})
        fold = SimpleNamespace(training_start=pd.Timestamp("2023-01-01"), training_end=pd.Timestamp("2023-01-31"),
            training_rows=1, model_name="test", period_start=pd.Timestamp("2023-02-01"))
        with patch("benchmarking.leakage_audit.build_feature_dataset", return_value=dataset) as build:
            failures = _check_fitted_on_training_rows_only({}, ["AAPL"], [fold], dataset_universe=None)
        self.assertEqual(failures, [])
        self.assertIsNone(build.call_args.kwargs["universe"])


if __name__ == "__main__":
    unittest.main()
