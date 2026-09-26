import unittest

import pandas as pd

from ml.dataset import (
    _universe_for_date,
    build_feature_dataset,
    build_quality_report,
    generate_evaluation_dates,
    get_supervised_subset,
)
from ml.features import REQUIRED_HISTORY_DAYS
from strategies.registry import available_strategy_names
from tests.ml_fixtures import make_multi_asset_price_data

N_ROWS = 500
TICKERS = ("AAPL", "MSFT")


def _price_data():
    return make_multi_asset_price_data(n=N_ROWS, tickers=TICKERS)


class EvaluationDateGenerationTests(unittest.TestCase):
    def test_weekly_dates_are_seven_days_apart(self):
        dates = generate_evaluation_dates("2022-01-01", "2022-03-01", frequency="weekly")
        self.assertGreater(len(dates), 1)
        gaps = [(later - earlier).days for earlier, later in zip(dates, dates[1:])]
        # The very first gap can be shorter (start_date is prepended even if
        # it doesn't fall on the weekly anchor day); every other gap is
        # exactly one week.
        self.assertTrue(all(0 < gap <= 7 for gap in gaps))
        self.assertEqual(gaps[1:], [7] * (len(gaps) - 1))

    def test_unknown_frequency_raises(self):
        with self.assertRaises(ValueError):
            generate_evaluation_dates("2022-01-01", "2022-03-01", frequency="hourly")


class PointInTimeUniverseTests(unittest.TestCase):
    """The default (no explicit universe) path must use the existing
    point-in-time Nasdaq-100 membership logic, unmodified."""

    def test_default_universe_reflects_a_known_membership_change(self):
        # META replaced FB effective 2022-06-09 (see data/historical_universe.py).
        superset = ["META", "FB", "AAPL"]
        before = pd.Timestamp("2022-06-08")
        after = pd.Timestamp("2022-06-10")

        before_universe = _universe_for_date(before, None, superset)
        after_universe = _universe_for_date(after, None, superset)

        self.assertIn("FB", before_universe)
        self.assertNotIn("META", before_universe)
        self.assertIn("META", after_universe)
        self.assertNotIn("FB", after_universe)

    def test_explicit_universe_overrides_point_in_time_membership(self):
        fixed = ["FAKE1", "FAKE2"]
        for date in (pd.Timestamp("2020-01-01"), pd.Timestamp("2024-01-01")):
            self.assertEqual(_universe_for_date(date, fixed, ["irrelevant"]), fixed)


class BuildFeatureDatasetTests(unittest.TestCase):
    def setUp(self):
        self.price_data = _price_data()
        self.start = self.price_data["AAPL"].index[REQUIRED_HISTORY_DAYS + 20]
        self.end = self.price_data["AAPL"].index[REQUIRED_HISTORY_DAYS + 120]

    def _build(self, price_data=None, **overrides):
        kwargs = dict(
            start_date=self.start,
            end_date=self.end,
            universe=list(TICKERS),
            rebalance_frequency="weekly",
            price_data=price_data if price_data is not None else self.price_data,
        )
        kwargs.update(overrides)
        return build_feature_dataset(**kwargs)

    def test_start_must_be_before_end(self):
        with self.assertRaises(ValueError):
            self._build(start_date=self.end, end_date=self.start)

    def test_produces_rows_for_every_ticker(self):
        dataset = self._build()
        self.assertFalse(dataset.empty)
        self.assertEqual(set(dataset["Ticker"].unique()), set(TICKERS))

    def test_deterministic_output(self):
        first = self._build()
        second = self._build()
        pd.testing.assert_frame_equal(
            first.reset_index(drop=True), second.reset_index(drop=True)
        )

    def test_no_lookahead_bias_in_features_or_near_labels(self):
        """A price shock far beyond the evaluation+label window must not
        change any feature or label value within that window."""
        shocked = {ticker: frame.copy() for ticker, frame in self.price_data.items()}
        shock_cutoff = self.end + pd.Timedelta(days=200)
        for ticker, frame in shocked.items():
            frame.loc[frame.index > shock_cutoff, "Close"] *= 50.0

        baseline_dataset = self._build()
        shocked_dataset = self._build(price_data=shocked)

        pd.testing.assert_frame_equal(
            baseline_dataset.reset_index(drop=True),
            shocked_dataset.reset_index(drop=True),
        )

    def test_insufficient_history_is_recorded_and_rows_are_skipped(self):
        # Give AAPL/MSFT only a short tail of history (so their per-ticker
        # REQUIRED_HISTORY_DAYS check fails for every evaluation date in the
        # window), while leaving SPY/QQQ's full history untouched so the
        # market-regime calculation itself still succeeds. This isolates the
        # per-ticker insufficient-history skip path from the separate
        # "missing regime data" failure path.
        limited_price_data = dict(self.price_data)
        for ticker in TICKERS:
            limited_price_data[ticker] = self.price_data[ticker].iloc[150:]

        dataset = self._build(price_data=limited_price_data)
        self.assertTrue(dataset.empty)
        self.assertGreater(dataset.attrs["insufficient_history_skips"], 0)

    def test_insufficient_future_label_rows_are_flagged_but_kept(self):
        # End near the very end of available price history so 60D forward
        # returns cannot be computed for the last few evaluation dates.
        late_start = self.price_data["AAPL"].index[N_ROWS - 80]
        late_end = self.price_data["AAPL"].index[N_ROWS - 5]
        dataset = self._build(start_date=late_start, end_date=late_end, label_horizon=60)
        self.assertGreater(dataset.attrs["insufficient_future_label_rows"], 0)
        self.assertTrue(dataset["Forward Return 60D"].isna().any())
        # Rows are still present (not silently dropped) so quality reporting
        # can distinguish "missing" from "never computed".
        self.assertGreater(len(dataset), 0)

    def test_get_supervised_subset_drops_missing_targets(self):
        late_start = self.price_data["AAPL"].index[N_ROWS - 80]
        late_end = self.price_data["AAPL"].index[N_ROWS - 5]
        dataset = self._build(start_date=late_start, end_date=late_end)
        supervised = get_supervised_subset(dataset, "beats_SPY_20D")
        self.assertTrue(supervised["beats_SPY_20D"].notna().all())
        self.assertLessEqual(len(supervised), len(dataset))

    def test_quality_report_matches_dataset_attrs(self):
        dataset = self._build()
        report = build_quality_report(dataset)
        self.assertEqual(report["Rows"], len(dataset))
        self.assertEqual(
            report["Rows Excluded - Insufficient History"],
            dataset.attrs["insufficient_history_skips"],
        )
        self.assertEqual(set(dataset["Ticker"].unique()), set(TICKERS))
        self.assertEqual(report["Unique Tickers"], len(TICKERS))


class ExistingStrategiesUnaffectedTests(unittest.TestCase):
    """Building ML datasets must not disturb the strategy registry."""

    def test_known_strategies_still_registered(self):
        names = available_strategy_names()
        for expected in ("Baseline", "Momentum V2", "Mean Reversion V1"):
            self.assertIn(expected, names)


if __name__ == "__main__":
    unittest.main()
