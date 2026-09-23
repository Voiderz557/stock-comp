"""Reusable historical feature-dataset builder.

`build_feature_dataset(...)` is the main entry point: for a date range, a
universe, and a rebalance frequency, it produces one row per
(ticker, evaluation date) containing point-in-time-safe features (see
`ml.features`) plus forward-return labels (see `ml.labels`), and returns a
single deterministic `pandas.DataFrame`.

POINT-IN-TIME UNIVERSE
-----------------------
When `universe` is not given, the per-date candidate list is exactly the
Nasdaq-100 snapshot active on that date (`data.historical_universe`), the
same point-in-time membership logic the backtesting engine itself uses -
this file does not reimplement or alter that logic, it only calls it.

CACHING / FAILURES / PROGRESS
------------------------------
- `price_data` can be supplied directly (dict of ticker -> DataFrame) to
  skip network/database loading entirely - this is what makes the dataset
  builder deterministic and easy to unit test.
- `cache_path` optionally persists (and, if `use_cache=True`, reuses) the
  *final* built dataset as a single CSV/Parquet file.
- Every per-ticker/date failure is caught and recorded rather than aborting
  the whole run; `build_quality_report` turns those records into a summary.
- `progress_callback(completed, total, date)` is invoked once per
  evaluation date.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from data.historical_universe import (
    HISTORICAL_UNIVERSE_START,
    get_backtest_tickers,
    get_historical_universe,
)
from data.market_data import load_market_data
from market.regime import classify_market_regime, compute_market_features
from ml.dates import price_history_start
from ml.features import (
    BENCHMARK_TICKERS,
    REQUIRED_HISTORY_DAYS,
    InsufficientHistoryError,
    compute_feature_row,
    register_price_universe,
    truncate_to_as_of,
)
from ml.labels import FORWARD_RETURN_HORIZONS_DAYS, compute_labels, forward_return_column

FREQUENCY_ALIASES = {
    "daily": "D",
    "weekly": "W-FRI",
    "monthly": "MS",
}

# Extra calendar-day buffer multipliers applied to trading-day requirements,
# to comfortably absorb weekends/holidays when deciding how much history to
# load - mirrors `backtesting.engine.download_backtest_data`'s convention.
HISTORY_WARMUP_CALENDAR_MULTIPLIER = 3
LABEL_LOOKAHEAD_CALENDAR_MULTIPLIER = 3

# A ticker whose most recent available row is more than this many calendar
# days before the nominal evaluation date is treated as too stale to score
# (e.g. delisted, acquired, or simply missing data) rather than silently
# reusing an old price.
DEFAULT_MAX_STALE_TRADING_GAP_DAYS = 10


def generate_evaluation_dates(start_date, end_date, frequency="weekly"):
    """Candidate calendar dates for feature rows, at the given frequency.

    These are calendar dates, not guaranteed trading days - each ticker's
    actual "as of" row is snapped to its own most recent trading day at or
    before the nominal date (see `build_feature_dataset`), so a holiday or
    weekend evaluation date is not an error.
    """
    if frequency not in FREQUENCY_ALIASES:
        raise ValueError(
            f"Unknown rebalance_frequency '{frequency}'. "
            f"Choose from {sorted(FREQUENCY_ALIASES)}."
        )
    start_date = pd.Timestamp(start_date).normalize()
    end_date = pd.Timestamp(end_date).normalize()
    dates = pd.date_range(start_date, end_date, freq=FREQUENCY_ALIASES[frequency])
    if len(dates) == 0 or dates[0] > start_date:
        dates = pd.DatetimeIndex([start_date]).append(pd.DatetimeIndex(dates))
    return list(pd.DatetimeIndex(dates).unique().sort_values())


def _universe_for_date(date, fixed_universe, superset):
    if fixed_universe is not None:
        return fixed_universe
    snapshot_tickers = set(get_historical_universe(date).tickers)
    return [ticker for ticker in superset if ticker in snapshot_tickers]


def _build_one_ticker_row(
    ticker,
    feature_date,
    ticker_full,
    spy_hist,
    qqq_hist,
    regime_result,
    benchmark_closes,
    benchmark_tickers,
    include_strategy_features,
    max_stale_trading_gap_days,
    label_horizon,
):
    if ticker in benchmark_tickers:
        return None
    if ticker_full is None or ticker_full.empty:
        return (
            "fail",
            {"Ticker": ticker, "Date": feature_date, "Reason": "No price data available."},
        )
    hist = truncate_to_as_of(ticker_full, feature_date)
    if hist.empty:
        return (
            "fail",
            {
                "Ticker": ticker,
                "Date": feature_date,
                "Reason": "No rows at or before the evaluation date.",
            },
        )
    actual_feature_date = hist.index[-1]
    staleness_days = (feature_date - actual_feature_date).days
    if staleness_days > max_stale_trading_gap_days:
        return (
            "fail",
            {
                "Ticker": ticker,
                "Date": feature_date,
                "Reason": (
                    f"Most recent available row ({actual_feature_date.date()}) "
                    f"is {staleness_days} calendar days stale."
                ),
            },
        )
    if len(hist) < REQUIRED_HISTORY_DAYS:
        return ("insufficient_history", None)
    try:
        feature_values = compute_feature_row(
            ticker,
            hist,
            spy_hist,
            qqq_hist,
            regime_result=regime_result,
            include_strategy_features=include_strategy_features,
        )
    except InsufficientHistoryError:
        return ("insufficient_history", None)
    except Exception as error:  # noqa: BLE001 - per-ticker isolation is intentional
        return (
            "fail",
            {
                "Ticker": ticker,
                "Date": feature_date,
                "Reason": f"Feature computation failed: {error}",
            },
        )
    label_values = compute_labels(
        ticker_full["Close"],
        benchmark_closes,
        actual_feature_date,
        primary_horizon=label_horizon,
    )
    row = {
        "Ticker": ticker,
        "Date": feature_date,
        "As Of Trading Date": actual_feature_date,
    }
    row.update(feature_values)
    row.update(label_values)
    status = (
        "insufficient_future"
        if pd.isna(label_values.get(forward_return_column(label_horizon)))
        else "row"
    )
    return (status, row)


def build_feature_dataset(
    start_date,
    end_date,
    universe=None,
    rebalance_frequency="weekly",
    label_horizon=20,
    price_data=None,
    benchmark_tickers=BENCHMARK_TICKERS,
    include_strategy_features=True,
    max_stale_trading_gap_days=DEFAULT_MAX_STALE_TRADING_GAP_DAYS,
    cache_path=None,
    use_cache=True,
    progress_callback=None,
    status_callback=None,
):
    """Build the historical feature+label dataset described in the module docstring.

    Parameters mirror the requested API: `start_date`/`end_date` bound the
    evaluation window, `universe` overrides the default point-in-time
    Nasdaq-100 membership (pass an explicit ticker list to pin a fixed
    universe for every date instead), `rebalance_frequency` controls
    evaluation-date sampling ("daily"/"weekly"/"monthly"), and
    `label_horizon` selects which forward-return horizon feeds the
    horizon-specific classification columns (the fixed `..._20D` columns
    are always computed in addition, per `ml.labels`).

    Returns a `pandas.DataFrame`, sorted deterministically by (Date,
    Ticker). Build metadata used by `build_quality_report` is attached via
    `dataset.attrs` (present when the DataFrame is returned in-process; not
    guaranteed to survive a CSV/Parquet round trip).
    """
    requested_start_date = pd.Timestamp(start_date).normalize()
    requested_end_date = pd.Timestamp(end_date).normalize()
    if requested_start_date >= requested_end_date:
        raise ValueError("start_date must be before end_date.")
    # Observation / universe-membership dates. Warmup prices may go earlier.
    evaluation_start_date = requested_start_date
    evaluation_end_date = requested_end_date
    start_date = evaluation_start_date
    end_date = evaluation_end_date
    if universe is None and evaluation_start_date < HISTORICAL_UNIVERSE_START:
        raise ValueError(
            f"Dataset observation start {evaluation_start_date.date()} is before "
            f"{HISTORICAL_UNIVERSE_START.date()}, the earliest supported Nasdaq-100 "
            "point-in-time membership date. Choose a later start date, or pass an "
            "explicit universe list. Price history warmup may still precede that date."
        )

    cache_path = Path(cache_path) if cache_path else None
    if cache_path is not None and use_cache and cache_path.exists():
        return load_dataset(cache_path)

    evaluation_dates = generate_evaluation_dates(start_date, end_date, rebalance_frequency)
    fixed_universe = list(dict.fromkeys(universe)) if universe is not None else None
    superset = (
        fixed_universe
        if fixed_universe is not None
        else get_backtest_tickers(start_date, end_date)
    )
    all_tickers = sorted(set(superset) | set(benchmark_tickers))

    # PRICE history only. This may precede HISTORICAL_UNIVERSE_START.
    # Universe membership is resolved from evaluation_start_date, never here.
    warmup_start = price_history_start(evaluation_start_date, REQUIRED_HISTORY_DAYS)
    label_end = end_date + pd.Timedelta(
        days=max(FORWARD_RETURN_HORIZONS_DAYS) * LABEL_LOOKAHEAD_CALENDAR_MULTIPLIER
    )

    if price_data is None:
        price_data, _cache_report = load_market_data(
            all_tickers, warmup_start, label_end, status_callback=status_callback
        )

    register_price_universe(price_data)
    cleaned_prices = {
        ticker: (
            frame.dropna(subset=["Close"])
            if frame is not None and not frame.empty and "Close" in frame.columns
            else frame
        )
        for ticker, frame in price_data.items()
    }

    rows = []
    insufficient_history_skips = 0
    insufficient_future_label_rows = 0
    failed_ticker_dates = []
    total_steps = len(evaluation_dates)

    for step, feature_date in enumerate(evaluation_dates):
        spy_full = cleaned_prices.get("SPY")
        qqq_full = cleaned_prices.get("QQQ")
        if spy_full is None or spy_full.empty or qqq_full is None or qqq_full.empty:
            failed_ticker_dates.append(
                {
                    "Ticker": None,
                    "Date": feature_date,
                    "Reason": "Missing SPY/QQQ benchmark data.",
                }
            )
            if progress_callback:
                progress_callback(step + 1, total_steps, feature_date)
            continue

        spy_hist = truncate_to_as_of(spy_full, feature_date)
        qqq_hist = truncate_to_as_of(qqq_full, feature_date)
        try:
            regime_result = classify_market_regime(
                compute_market_features(spy_hist, qqq_hist)
            )
        except ValueError as error:
            failed_ticker_dates.append(
                {"Ticker": None, "Date": feature_date, "Reason": str(error)}
            )
            if progress_callback:
                progress_callback(step + 1, total_steps, feature_date)
            continue

        date_universe = [
            ticker
            for ticker in _universe_for_date(feature_date, fixed_universe, superset)
            if ticker not in benchmark_tickers
        ]
        benchmark_closes = {
            name: cleaned_prices[name]["Close"]
            for name in benchmark_tickers
            if cleaned_prices.get(name) is not None
        }

        computed = [
            _build_one_ticker_row(
                ticker,
                feature_date,
                cleaned_prices.get(ticker),
                spy_hist,
                qqq_hist,
                regime_result,
                benchmark_closes,
                benchmark_tickers,
                include_strategy_features,
                max_stale_trading_gap_days,
                label_horizon,
            )
            for ticker in date_universe
        ]

        for result in computed:
            if result is None:
                continue
            status, payload = result
            if status == "row":
                rows.append(payload)
            elif status == "insufficient_future":
                insufficient_future_label_rows += 1
                rows.append(payload)
            elif status == "insufficient_history":
                insufficient_history_skips += 1
            elif status == "fail":
                failed_ticker_dates.append(payload)

        if progress_callback:
            progress_callback(step + 1, total_steps, feature_date)

    dataset = pd.DataFrame(rows)
    if not dataset.empty:
        dataset = dataset.sort_values(["Date", "Ticker"]).reset_index(drop=True)

    dataset.attrs["build_parameters"] = {
        "start_date": str(start_date.date()),
        "end_date": str(end_date.date()),
        "rebalance_frequency": rebalance_frequency,
        "label_horizon": label_horizon,
        "universe_size": len(superset),
        "include_strategy_features": include_strategy_features,
    }
    dataset.attrs["insufficient_history_skips"] = insufficient_history_skips
    dataset.attrs["insufficient_future_label_rows"] = insufficient_future_label_rows
    dataset.attrs["failed_ticker_dates"] = failed_ticker_dates
    dataset.attrs["evaluation_dates"] = [str(date.date()) for date in evaluation_dates]

    if cache_path is not None:
        export_dataset(dataset, cache_path)

    return dataset


def export_dataset_csv(dataset, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    dataset.to_csv(path, index=False)
    return path


def export_dataset_parquet(dataset, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    dataset.to_parquet(path, index=False)
    return path


def export_dataset(dataset, path):
    """Export to CSV or Parquet, chosen by `path`'s file extension."""
    path = Path(path)
    if path.suffix.lower() == ".parquet":
        return export_dataset_parquet(dataset, path)
    return export_dataset_csv(dataset, path)


def load_dataset(path):
    path = Path(path)
    if path.suffix.lower() == ".parquet":
        return pd.read_parquet(path)
    return pd.read_csv(path, parse_dates=["Date", "As Of Trading Date"])


def get_supervised_subset(dataset, target_column):
    """Rows with a non-missing `target_column` - safe for supervised training.

    Rows excluded here are exactly the ones without enough future history
    to compute that label (see `ml.labels`).
    """
    if dataset is None or dataset.empty:
        return dataset.copy() if dataset is not None else pd.DataFrame()
    if target_column not in dataset.columns:
        raise KeyError(f"Unknown target column '{target_column}'.")
    return dataset.dropna(subset=[target_column]).reset_index(drop=True)


def build_quality_report(dataset, primary_target_column="beats_SPY_20D"):
    """Summarize dataset size, coverage, missingness, and class balance."""
    report = {}
    report["Rows"] = int(len(dataset))
    report["Unique Tickers"] = (
        int(dataset["Ticker"].nunique()) if "Ticker" in dataset.columns else 0
    )
    if "Date" in dataset.columns and not dataset.empty:
        dates = pd.to_datetime(dataset["Date"])
        report["Date Range"] = (str(dates.min().date()), str(dates.max().date()))
    else:
        report["Date Range"] = (None, None)
    report["Missing Value Counts"] = {
        str(column): int(count) for column, count in dataset.isna().sum().items()
    }

    attrs = getattr(dataset, "attrs", {}) or {}
    report["Build Parameters"] = attrs.get("build_parameters", {})
    report["Rows Excluded - Insufficient History"] = attrs.get("insufficient_history_skips")
    report["Rows With Insufficient Future Label Data"] = attrs.get(
        "insufficient_future_label_rows"
    )
    report["Failed Ticker/Date Calculations"] = attrs.get("failed_ticker_dates", [])

    if primary_target_column in dataset.columns:
        valid_targets = dataset[primary_target_column].dropna()
        report["Class Balance"] = (
            {str(key): float(value) for key, value in valid_targets.value_counts(normalize=True).items()}
            if not valid_targets.empty
            else {}
        )
    else:
        report["Class Balance"] = {}
    return report


def export_quality_report_json(report, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return path
