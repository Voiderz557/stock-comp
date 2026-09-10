"""Shared synthetic-data helpers for the `ml` package test suite.

Not itself a test module (no `test_` prefix), so pytest/unittest discovery
skips it, but it is imported by the `test_ml_*.py` files below.
"""

import numpy as np
import pandas as pd


def make_price_frame(n, start="2018-01-01", drift=0.0004, vol=0.01, start_price=100.0, seed=0):
    """A deterministic (seeded) synthetic daily OHLCV-ish price frame."""
    rng = np.random.RandomState(seed)
    dates = pd.bdate_range(start, periods=n)
    returns = rng.normal(drift, vol, size=n)
    prices = start_price * np.cumprod(1 + returns)
    volumes = rng.randint(1_000_000, 5_000_000, size=n)
    return pd.DataFrame(
        {"Open": prices, "Close": prices, "Volume": volumes}, index=dates
    )


def make_multi_asset_price_data(
    n=500,
    tickers=("AAPL", "MSFT", "NVDA", "AMZN"),
    start="2018-01-01",
    seed_offset=10,
):
    """SPY + QQQ + a handful of synthetic tickers, all deterministic."""
    data = {
        "SPY": make_price_frame(n, start=start, drift=0.0003, vol=0.008, start_price=400.0, seed=1),
        "QQQ": make_price_frame(n, start=start, drift=0.0004, vol=0.010, start_price=350.0, seed=2),
    }
    for index, ticker in enumerate(tickers):
        data[ticker] = make_price_frame(
            n,
            start=start,
            drift=0.0003 + index * 0.0001,
            vol=0.015 + index * 0.001,
            start_price=100.0 + index * 20,
            seed=seed_offset + index,
        )
    return data


SAMPLE_MODEL_TICKERS = ("AAPL", "MSFT", "NVDA", "AMZN")
SAMPLE_MODEL_N_ROWS = 650


def build_sample_supervised_dataset(
    tickers=SAMPLE_MODEL_TICKERS, n_rows=SAMPLE_MODEL_N_ROWS, target_column="beats_SPY_20D"
):
    """A moderately sized, deterministic, in-memory dataset for model/eval tests.

    Kept in one place so `test_ml_models.py` and `test_ml_evaluation.py` build
    it identically without duplicating the setup logic.
    """
    from ml.dataset import build_feature_dataset, get_supervised_subset
    from ml.features import REQUIRED_HISTORY_DAYS

    price_data = make_multi_asset_price_data(n=n_rows, tickers=tickers, seed_offset=10)
    start = price_data["AAPL"].index[REQUIRED_HISTORY_DAYS + 10]
    end = price_data["AAPL"].index[n_rows - 60]
    dataset = build_feature_dataset(
        start,
        end,
        universe=list(tickers),
        rebalance_frequency="weekly",
        price_data=price_data,
    )
    return get_supervised_subset(dataset, target_column)


def linear_frame(length, slope=0.5, start_price=100.0, start="2018-01-01", volume=1_000_000):
    """A perfectly linear (noise-free) price series, handy for exact-value tests."""
    dates = pd.bdate_range(start, periods=length)
    closes = [start_price + index * slope for index in range(length)]
    return pd.DataFrame(
        {"Open": closes, "Close": closes, "Volume": [volume] * length}, index=dates
    )
