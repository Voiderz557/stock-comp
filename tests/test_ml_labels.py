import unittest

import numpy as np
import pandas as pd

from ml.labels import (
    FIXED_CLASSIFICATION_COLUMNS,
    PRIMARY_LABEL_HORIZON_DAYS,
    compute_forward_return,
    compute_labels,
    has_sufficient_future_history,
    label_column_names,
)
from tests.ml_fixtures import make_multi_asset_price_data


class ForwardReturnTests(unittest.TestCase):
    def test_exact_forward_return_value(self):
        dates = pd.bdate_range("2020-01-01", periods=30)
        closes = pd.Series([100.0 + index for index in range(30)], index=dates)
        feature_date = dates[10]
        expected = closes.iloc[15] / closes.iloc[10] - 1
        self.assertAlmostEqual(
            compute_forward_return(closes, feature_date, 5), expected
        )

    def test_missing_future_data_returns_nan(self):
        dates = pd.bdate_range("2020-01-01", periods=10)
        closes = pd.Series(range(100, 110), index=dates)
        feature_date = dates[8]
        self.assertTrue(pd.isna(compute_forward_return(closes, feature_date, 5)))

    def test_date_not_present_returns_nan(self):
        dates = pd.bdate_range("2020-01-01", periods=10)
        closes = pd.Series(range(100, 110), index=dates)
        missing_date = pd.Timestamp("2019-01-01")
        self.assertTrue(pd.isna(compute_forward_return(closes, missing_date, 5)))

    def test_has_sufficient_future_history_matches_forward_return_availability(self):
        dates = pd.bdate_range("2020-01-01", periods=10)
        closes = pd.Series(range(100, 110), index=dates)
        feature_date = dates[8]
        self.assertFalse(has_sufficient_future_history(closes, feature_date, 5))
        self.assertTrue(has_sufficient_future_history(closes, feature_date, 1))


class LabelsChangeWithFutureDataTests(unittest.TestCase):
    """Labels DO depend on future rows - the opposite contract from features."""

    def setUp(self):
        n = 200
        self.price_data = make_multi_asset_price_data(n=n, tickers=("AAPL",))
        self.feature_date = self.price_data["AAPL"].index[150]

    def _labels(self, aapl_closes, spy_closes, qqq_closes):
        return compute_labels(
            aapl_closes, {"SPY": spy_closes, "QQQ": qqq_closes}, self.feature_date
        )

    def test_labels_change_when_a_later_price_changes(self):
        aapl = self.price_data["AAPL"]["Close"]
        spy = self.price_data["SPY"]["Close"]
        qqq = self.price_data["QQQ"]["Close"]

        baseline_labels = self._labels(aapl, spy, qqq)

        shocked_aapl = aapl.copy()
        # 20 trading rows after the feature date - squarely inside the 20D
        # and 60D forward-return windows, but outside the 5D window.
        shock_position = aapl.index.get_loc(self.feature_date) + 20
        shocked_aapl.iloc[shock_position] *= 2.0
        shocked_labels = self._labels(shocked_aapl, spy, qqq)

        self.assertEqual(
            baseline_labels["Forward Return 5D"], shocked_labels["Forward Return 5D"]
        )
        self.assertNotEqual(
            baseline_labels["Forward Return 20D"], shocked_labels["Forward Return 20D"]
        )
        self.assertNotEqual(
            baseline_labels["Forward Return 60D"], shocked_labels["Forward Return 60D"]
        )

    def test_forward_returns_beyond_available_history_are_nan_near_the_end(self):
        aapl = self.price_data["AAPL"]["Close"]
        spy = self.price_data["SPY"]["Close"]
        qqq = self.price_data["QQQ"]["Close"]
        near_end_date = aapl.index[-3]
        labels = compute_labels(aapl, {"SPY": spy, "QQQ": qqq}, near_end_date)
        self.assertTrue(pd.isna(labels["Forward Return 5D"]))
        self.assertTrue(pd.isna(labels["Forward Return 60D"]))
        self.assertTrue(pd.isna(labels["beats_SPY_20D"]))


class ClassificationTargetTests(unittest.TestCase):
    def test_beats_benchmark_definition(self):
        dates = pd.bdate_range("2020-01-01", periods=40)
        stock_closes = pd.Series([100.0 * (1.01 ** index) for index in range(40)], index=dates)
        spy_closes = pd.Series([100.0 * (1.005 ** index) for index in range(40)], index=dates)
        qqq_closes = pd.Series([100.0 * (1.02 ** index) for index in range(40)], index=dates)
        feature_date = dates[10]

        labels = compute_labels(
            stock_closes, {"SPY": spy_closes, "QQQ": qqq_closes}, feature_date
        )
        # Stock (1% daily) beats SPY (0.5% daily) but not QQQ (2% daily) over 20D.
        self.assertTrue(labels["beats_SPY_20D"])
        self.assertFalse(labels["beats_QQQ_20D"])
        self.assertTrue(labels["positive_20D"])

    def test_return_ge_5pct_threshold_is_inclusive(self):
        dates = pd.bdate_range("2020-01-01", periods=30)
        # Construct a series whose 20D forward return is exactly 5%.
        closes = [100.0] * 30
        closes[25] = 105.0
        stock_closes = pd.Series(closes, index=dates)
        flat_benchmark = pd.Series([100.0] * 30, index=dates)
        feature_date = dates[5]

        labels = compute_labels(
            stock_closes, {"SPY": flat_benchmark, "QQQ": flat_benchmark}, feature_date
        )
        self.assertAlmostEqual(labels["Forward Return 20D"], 0.05)
        self.assertTrue(labels["return_ge_5pct_20D"])

    def test_fixed_classification_columns_always_present(self):
        dates = pd.bdate_range("2020-01-01", periods=100)
        closes = pd.Series(np.linspace(100, 150, 100), index=dates)
        feature_date = dates[50]
        labels = compute_labels(closes, {"SPY": closes, "QQQ": closes}, feature_date)
        for column in FIXED_CLASSIFICATION_COLUMNS:
            self.assertIn(column, labels)

    def test_label_column_names_is_a_superset_of_fixed_columns(self):
        names = label_column_names()
        for column in FIXED_CLASSIFICATION_COLUMNS:
            self.assertIn(column, names)
        self.assertIn(f"Forward Return {PRIMARY_LABEL_HORIZON_DAYS}D", names)


if __name__ == "__main__":
    unittest.main()
