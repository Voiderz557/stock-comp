"""Named date roles for the ML research / benchmark stack.

Callers must not pass a generic ``start`` into both price warmup and
Nasdaq-100 membership lookup. This module makes the roles explicit.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from data.historical_universe import HISTORICAL_UNIVERSE_START

HISTORY_WARMUP_CALENDAR_MULTIPLIER = 3


def as_timestamp(value):
    return pd.Timestamp(value).normalize()


def price_history_start(evaluation_or_training_start, required_history_days):
    """PRICE-ONLY warmup start. May precede `HISTORICAL_UNIVERSE_START`."""
    return as_timestamp(evaluation_or_training_start) - pd.Timedelta(
        days=int(required_history_days) * HISTORY_WARMUP_CALENDAR_MULTIPLIER
    )


def assert_observation_dates_supported(observation_start, observation_end, *, context=""):
    """Observation/membership dates must lie on or after the universe boundary."""
    observation_start = as_timestamp(observation_start)
    observation_end = as_timestamp(observation_end)
    prefix = f"{context}: " if context else ""
    if observation_start >= observation_end:
        raise ValueError(f"{prefix}observation start must be before observation end.")
    if observation_start < HISTORICAL_UNIVERSE_START:
        raise ValueError(
            f"{prefix}observation start {observation_start.date()} is before "
            f"{HISTORICAL_UNIVERSE_START.date()}, the earliest supported Nasdaq-100 "
            "point-in-time membership date. Price warmup may go earlier; dataset "
            "observation dates and universe membership lookups may not."
        )
    return observation_start, observation_end


@dataclass(frozen=True)
class BenchmarkDatePlan:
    """Requested vs effective dates used by one benchmark run."""

    requested_start_date: pd.Timestamp
    requested_end_date: pd.Timestamp
    evaluation_start_date: pd.Timestamp
    evaluation_end_date: pd.Timestamp
    training_start_floor: pd.Timestamp
    price_history_start_date: pd.Timestamp
    test_count: int
    seed: int
    universe_mode: str
