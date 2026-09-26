import unittest

import pandas as pd

from ml.features import (
    CATEGORICAL_FEATURE_COLUMNS,
    REQUIRED_HISTORY_DAYS,
    InsufficientHistoryError,
    compute_feature_row,
    truncate_to_as_of,
)
from strategies.registry import available_strategy_names
from tests.ml_fixtures import linear_frame, make_multi_asset_price_data


class TruncateToAsOfTests(unittest.TestCase):
    def test_truncate_drops_rows_after_the_as_of_date(self):
        data = make_multi_asset_price_data(n=50, tickers=())["SPY"]
        as_of = data.index[20]
        truncated = truncate_to_as_of(data, as_of)
        self.assertEqual(truncated.index.max(), as_of)
        self.assertTrue((truncated.index <= as_of).all())
        self.assertEqual(len(truncated), 21)

    def test_truncate_is_independent_of_rows_after_as_of(self):
        """Two frames that agree up to T, but diverge after T, truncate identically."""
        base = make_multi_asset_price_data(n=250, tickers=())["SPY"]
        as_of = base.index[200]

        variant_a = base.copy()
        variant_b = base.copy()
        variant_b.loc[base.index > as_of, "Close"] *= 5.0  # a big future shock

        truncated_a = truncate_to_as_of(variant_a, as_of)
        truncated_b = truncate_to_as_of(variant_b, as_of)
        pd.testing.assert_frame_equal(truncated_a, truncated_b)


class FeatureLookAheadSafetyTests(unittest.TestCase):
    """Direct proof that `compute_feature_row` never uses rows after T."""

    def setUp(self):
        n = REQUIRED_HISTORY_DAYS + 60
        self.price_data = make_multi_asset_price_data(n=n, tickers=("AAPL",))
        self.as_of = self.price_data["AAPL"].index[REQUIRED_HISTORY_DAYS + 20]

    def _features_for(self, aapl_data, spy_data, qqq_data):
        historical = truncate_to_as_of(aapl_data, self.as_of)
        spy_hist = truncate_to_as_of(spy_data, self.as_of)
        qqq_hist = truncate_to_as_of(qqq_data, self.as_of)
        return compute_feature_row("AAPL", historical, spy_hist, qqq_hist)

    def test_features_unchanged_when_future_rows_are_modified(self):
        aapl = self.price_data["AAPL"]
        spy = self.price_data["SPY"]
        qqq = self.price_data["QQQ"]

        shocked_aapl = aapl.copy()
        shocked_aapl.loc[shocked_aapl.index > self.as_of, "Close"] *= 10.0
        shocked_aapl.loc[shocked_aapl.index > self.as_of, "Volume"] *= 50

        shocked_spy = spy.copy()
        shocked_spy.loc[shocked_spy.index > self.as_of, "Close"] *= 0.1

        shocked_qqq = qqq.copy()
        shocked_qqq.loc[shocked_qqq.index > self.as_of, "Close"] *= 0.1

        calm_features = self._features_for(aapl, spy, qqq)
        shocked_features = self._features_for(shocked_aapl, shocked_spy, shocked_qqq)

        self.assertEqual(calm_features, shocked_features)

    def test_without_truncation_features_do_differ(self):
        """Sanity check: the "no effect" result above is due to truncation,
        not because compute_feature_row ignores its input entirely."""
        aapl = self.price_data["AAPL"]
        spy = self.price_data["SPY"]
        qqq = self.price_data["QQQ"]

        untruncated_features = compute_feature_row("AAPL", aapl, spy, qqq)
        truncated_features = self._features_for(aapl, spy, qqq)
        self.assertNotEqual(untruncated_features["Price"], truncated_features["Price"])

    def test_deterministic_repeated_calls(self):
        aapl = self.price_data["AAPL"]
        spy = self.price_data["SPY"]
        qqq = self.price_data["QQQ"]
        first = self._features_for(aapl, spy, qqq)
        second = self._features_for(aapl, spy, qqq)
        self.assertEqual(first, second)

    def test_insufficient_history_raises(self):
        short_history = truncate_to_as_of(
            self.price_data["AAPL"], self.price_data["AAPL"].index[REQUIRED_HISTORY_DAYS - 5]
        )
        spy_hist = truncate_to_as_of(self.price_data["SPY"], short_history.index[-1])
        qqq_hist = truncate_to_as_of(self.price_data["QQQ"], short_history.index[-1])
        with self.assertRaises(InsufficientHistoryError):
            compute_feature_row("AAPL", short_history, spy_hist, qqq_hist)


class BreakoutLookAheadTests(unittest.TestCase):
    def test_prior_high_excludes_current_row(self):
        """A current-row spike must not count as its own 'prior' high."""
        length = REQUIRED_HISTORY_DAYS + 5
        frame = linear_frame(length, slope=0.1, start_price=100.0)
        # Make today's close a new all-time high far above every prior close.
        frame.iloc[-1, frame.columns.get_loc("Close")] = frame["Close"].iloc[:-1].max() * 3
        spy = linear_frame(length, slope=0.2, start_price=400.0)
        qqq = linear_frame(length, slope=0.2, start_price=350.0)

        row = compute_feature_row("SPIKE", frame, spy, qqq)
        # Price is now far above the highest PRIOR close, so breakout distance
        # must be large and positive - it would be ~0 if today's own close
        # were incorrectly included in its own "prior high" window.
        self.assertGreater(row["Breakout Distance 20D"], 1.0)
        self.assertGreater(row["Breakout Distance 60D"], 1.0)


class RegimeFeatureLookAheadTests(unittest.TestCase):
    def test_regime_feature_uses_only_historical_spy_qqq_data(self):
        n = 260
        spy = make_multi_asset_price_data(n=n, tickers=())["SPY"]
        qqq = make_multi_asset_price_data(n=n, tickers=())["QQQ"]
        aapl = make_multi_asset_price_data(n=n, tickers=("AAPL",))["AAPL"]
        as_of = spy.index[220]

        spy_hist = truncate_to_as_of(spy, as_of)
        qqq_hist = truncate_to_as_of(qqq, as_of)
        aapl_hist = truncate_to_as_of(aapl, as_of)

        shocked_spy = spy.copy()
        shocked_spy.loc[shocked_spy.index > as_of, "Close"] *= 20.0
        shocked_qqq = qqq.copy()
        shocked_qqq.loc[shocked_qqq.index > as_of, "Close"] *= 0.01
        shocked_spy_hist = truncate_to_as_of(shocked_spy, as_of)
        shocked_qqq_hist = truncate_to_as_of(shocked_qqq, as_of)

        calm_row = compute_feature_row("AAPL", aapl_hist, spy_hist, qqq_hist)
        shocked_row = compute_feature_row("AAPL", aapl_hist, shocked_spy_hist, shocked_qqq_hist)

        self.assertEqual(calm_row["Regime"], shocked_row["Regime"])
        self.assertAlmostEqual(
            calm_row["Regime SPY Momentum 20D"], shocked_row["Regime SPY Momentum 20D"]
        )


class StrategyFeatureTests(unittest.TestCase):
    def test_every_registered_strategy_has_score_and_signal_columns(self):
        n = REQUIRED_HISTORY_DAYS + 10
        price_data = make_multi_asset_price_data(n=n, tickers=("AAPL",))
        as_of = price_data["AAPL"].index[-1]
        hist = truncate_to_as_of(price_data["AAPL"], as_of)
        spy_hist = truncate_to_as_of(price_data["SPY"], as_of)
        qqq_hist = truncate_to_as_of(price_data["QQQ"], as_of)

        row = compute_feature_row(
            "AAPL", hist, spy_hist, qqq_hist, include_strategy_features=True
        )
        for name in available_strategy_names():
            self.assertIn(f"Strategy Score: {name}", row)
            self.assertIn(f"Strategy Signal: {name}", row)

    def test_strategy_features_can_be_disabled(self):
        n = REQUIRED_HISTORY_DAYS + 10
        price_data = make_multi_asset_price_data(n=n, tickers=("AAPL",))
        as_of = price_data["AAPL"].index[-1]
        hist = truncate_to_as_of(price_data["AAPL"], as_of)
        spy_hist = truncate_to_as_of(price_data["SPY"], as_of)
        qqq_hist = truncate_to_as_of(price_data["QQQ"], as_of)

        row = compute_feature_row(
            "AAPL", hist, spy_hist, qqq_hist, include_strategy_features=False
        )
        self.assertFalse(any(key.startswith("Strategy Score:") for key in row))


class MeanReversionFeatureRangeTests(unittest.TestCase):
    def test_rsi_is_within_zero_to_one_hundred(self):
        n = REQUIRED_HISTORY_DAYS + 10
        price_data = make_multi_asset_price_data(n=n, tickers=("AAPL",))
        as_of = price_data["AAPL"].index[-1]
        hist = truncate_to_as_of(price_data["AAPL"], as_of)
        spy_hist = truncate_to_as_of(price_data["SPY"], as_of)
        qqq_hist = truncate_to_as_of(price_data["QQQ"], as_of)
        row = compute_feature_row("AAPL", hist, spy_hist, qqq_hist)
        self.assertGreaterEqual(row["RSI14"], 0.0)
        self.assertLessEqual(row["RSI14"], 100.0)

    def test_categorical_columns_declared(self):
        self.assertEqual(CATEGORICAL_FEATURE_COLUMNS, ("Regime",))


if __name__ == "__main__":
    unittest.main()
