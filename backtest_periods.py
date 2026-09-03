"""Deterministic random test-window generation for the backtest UI."""

import random

import pandas as pd


DURATION_OPTIONS = {
    "1 month": pd.DateOffset(months=1),
    "2 months": pd.DateOffset(months=2),
    "3 months": pd.DateOffset(months=3),
    "5 months": pd.DateOffset(months=5),
    "6 months": pd.DateOffset(months=6),
    "1 year": pd.DateOffset(years=1),
}


def generate_random_periods(duration, earliest, latest, number_of_tests, seed):
    """Generate reproducible calendar windows wholly inside the boundaries."""
    if duration not in DURATION_OPTIONS:
        raise ValueError("Unknown test duration.")
    earliest = pd.Timestamp(earliest).normalize()
    latest = pd.Timestamp(latest).normalize()
    offset = DURATION_OPTIONS[duration]
    latest_start = latest - offset
    if earliest > latest_start:
        raise ValueError("The date boundaries are too narrow for this duration.")
    if number_of_tests < 1:
        raise ValueError("Number of tests must be positive.")

    available_days = (latest_start - earliest).days + 1
    rng = random.Random(int(seed))
    if number_of_tests <= available_days:
        day_offsets = rng.sample(range(available_days), number_of_tests)
    else:
        day_offsets = [rng.randrange(available_days) for _ in range(number_of_tests)]

    return [
        (
            earliest + pd.Timedelta(days=days),
            earliest + pd.Timedelta(days=days) + offset,
        )
        for days in day_offsets
    ]
