import unittest

import pandas as pd

from benchmarking.rule_validation import validate_chronological_folds


class RuleValidationTests(unittest.TestCase):
    def test_accepts_ordered_non_overlapping_folds(self):
        folds = validate_chronological_folds(
            [("2024-01-01", "2024-01-31"), ("2024-02-01", "2024-02-29")]
        )
        self.assertEqual(folds[0][0], pd.Timestamp("2024-01-01"))

    def test_rejects_overlapping_or_reversed_folds(self):
        with self.assertRaisesRegex(ValueError, "non-overlapping"):
            validate_chronological_folds(
                [("2024-01-01", "2024-02-01"), ("2024-02-01", "2024-03-01")]
            )
        with self.assertRaisesRegex(ValueError, "start < end"):
            validate_chronological_folds([("2024-02-01", "2024-01-01")])


if __name__ == "__main__":
    unittest.main()
