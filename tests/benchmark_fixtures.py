"""Shared synthetic-data helpers for the `benchmarking` package test suite.

Not itself a test module (no `test_` prefix), so pytest/unittest discovery
skips it, but it is imported by the `test_benchmarking_*.py` files below.
"""

from tests.ml_fixtures import make_multi_asset_price_data

# ~6.2 real years of business days, so there is always enough history before
# the earliest test period for BOTH the ~200-trading-day feature warm-up
# AND a short ML training window.
BENCHMARK_TICKERS = ("AAPL", "MSFT", "NVDA", "AMZN")
BENCHMARK_N_ROWS = 1600
BENCHMARK_START = "2014-01-02"

# Deliberately small so per-period ML training in tests stays fast - real
# usage (the Streamlit UI) uses the ~3-year `ml_training.TRAINING_WINDOW_CALENDAR_DAYS` default.
TEST_TRAINING_WINDOW_DAYS = 260


def build_benchmark_price_data():
    return make_multi_asset_price_data(
        n=BENCHMARK_N_ROWS, tickers=BENCHMARK_TICKERS, start=BENCHMARK_START, seed_offset=20
    )


def benchmark_period_bounds(price_data, earliest_index=950, latest_index=1520):
    """A `(earliest_allowed, latest_allowed)` pair with a safe amount of
    history before `earliest_index` and trading days remaining after
    `latest_index`, for a benchmark ticker's date index."""
    dates = price_data["AAPL"].index
    return dates[earliest_index], dates[latest_index]
