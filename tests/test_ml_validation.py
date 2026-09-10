import unittest

import pandas as pd

from ml.validation import (
    ROLLING_WINDOW,
    assert_no_temporal_leakage,
    generate_walk_forward_folds,
    split_dataset_by_fold,
)


class WalkForwardFoldGenerationTests(unittest.TestCase):
    def test_train_end_always_before_validation_start(self):
        folds = generate_walk_forward_folds(
            "2020-01-01", "2022-01-01", min_train_days=200, validation_days=60, step_days=60
        )
        self.assertGreater(len(folds), 0)
        for fold in folds:
            self.assertLess(fold.train_end, fold.validation_start)
            self.assertLessEqual(fold.train_start, fold.train_end)
            self.assertLessEqual(fold.validation_start, fold.validation_end)

    def test_folds_never_extend_past_end_date(self):
        end_date = pd.Timestamp("2021-06-01")
        folds = generate_walk_forward_folds(
            "2020-01-01", end_date, min_train_days=150, validation_days=45, step_days=45
        )
        for fold in folds:
            self.assertLessEqual(fold.validation_end, end_date)

    def test_expanding_window_keeps_train_start_fixed(self):
        folds = generate_walk_forward_folds(
            "2020-01-01", "2022-01-01", min_train_days=200, validation_days=60, step_days=60
        )
        first_train_start = folds[0].train_start
        for fold in folds:
            self.assertEqual(fold.train_start, first_train_start)
            self.assertGreaterEqual(fold.train_end, first_train_start)
        # Training window should grow across folds.
        self.assertGreater(folds[-1].train_end, folds[0].train_end)

    def test_rolling_window_slides_train_start_forward(self):
        folds = generate_walk_forward_folds(
            "2020-01-01",
            "2022-01-01",
            min_train_days=200,
            validation_days=60,
            step_days=60,
            window_mode=ROLLING_WINDOW,
        )
        self.assertGreater(len(folds), 1)
        self.assertGreater(folds[-1].train_start, folds[0].train_start)
        # Rolling windows keep a roughly constant training length.
        first_length = (folds[0].train_end - folds[0].train_start).days
        last_length = (folds[-1].train_end - folds[-1].train_start).days
        self.assertEqual(first_length, last_length)

    def test_invalid_window_mode_raises(self):
        with self.assertRaises(ValueError):
            generate_walk_forward_folds(
                "2020-01-01", "2021-01-01", window_mode="not-a-real-mode"
            )

    def test_start_after_end_raises(self):
        with self.assertRaises(ValueError):
            generate_walk_forward_folds("2021-01-01", "2020-01-01")


class SplitAndLeakageGuardTests(unittest.TestCase):
    def setUp(self):
        self.folds = generate_walk_forward_folds(
            "2020-01-01", "2021-01-01", min_train_days=180, validation_days=60, step_days=60
        )
        dates = pd.date_range("2020-01-01", "2021-01-01", freq="D")
        self.dataset = pd.DataFrame({"Date": dates, "Value": range(len(dates))})

    def test_split_produces_disjoint_non_empty_frames(self):
        fold = self.folds[0]
        train_df, validation_df = split_dataset_by_fold(self.dataset, fold)
        self.assertFalse(train_df.empty)
        self.assertFalse(validation_df.empty)
        overlap = set(train_df["Date"]) & set(validation_df["Date"])
        self.assertEqual(overlap, set())

    def test_no_row_appears_in_both_train_and_validation_across_all_folds(self):
        for fold in self.folds:
            train_df, validation_df = split_dataset_by_fold(self.dataset, fold)
            assert_no_temporal_leakage(train_df, validation_df)  # must not raise
            overlap = set(train_df["Date"]) & set(validation_df["Date"])
            self.assertEqual(overlap, set())

    def test_assert_no_temporal_leakage_raises_on_overlap(self):
        dates = pd.date_range("2020-01-01", periods=20, freq="D")
        overlapping_train = pd.DataFrame({"Date": dates[:12]})
        overlapping_validation = pd.DataFrame({"Date": dates[8:]})
        with self.assertRaises(ValueError):
            assert_no_temporal_leakage(overlapping_train, overlapping_validation)

    def test_assert_no_temporal_leakage_allows_disjoint_frames(self):
        dates = pd.date_range("2020-01-01", periods=20, freq="D")
        train = pd.DataFrame({"Date": dates[:10]})
        validation = pd.DataFrame({"Date": dates[10:]})
        assert_no_temporal_leakage(train, validation)  # must not raise


if __name__ == "__main__":
    unittest.main()
