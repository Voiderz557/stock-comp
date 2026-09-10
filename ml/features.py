"""Point-in-time feature engineering for the ML research foundation.

LOOK-AHEAD SAFETY CONTRACT
---------------------------
Every function below is a pure calculation over price-history DataFrames
that the caller has already truncated to rows at or before an evaluation
("as of") date `T`. Nothing here loads data, reads the wall clock, or
inspects any row dated after `T`. `truncate_to_as_of` is the single,
explicit, auditable helper responsible for enforcing that boundary - every
other function in this module trusts that its input has already gone
through it (see `ml/dataset.py`, which is the only place in this package
that decides what "as of T" means for a given ticker).

Future prices are NEVER read in this module. Forward returns and
classification targets (which necessarily need future data) live entirely
in `ml/labels.py`.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from market.regime import (
    REQUIRED_HISTORY_DAYS as REGIME_REQUIRED_HISTORY_DAYS,
    classify_market_regime,
    compute_market_features,
)
from strategies.mean_reversion_v1 import calculate_rsi
from strategies.registry import available_strategy_names, get_strategy, invoke_analyze

# ---------------------------------------------------------------------------
# Lookback windows (trading days). Every threshold here is a named constant,
# consistent with the rest of the project - nothing is tuned against
# historical results.
# ---------------------------------------------------------------------------
MOVING_AVERAGE_20D_DAYS = 20
MOVING_AVERAGE_50D_DAYS = 50
MOVING_AVERAGE_200D_DAYS = 200
MA_SLOPE_LOOKBACK_DAYS = 5

MOMENTUM_5D_DAYS = 5
MOMENTUM_20D_DAYS = 20
MOMENTUM_60D_DAYS = 60
MOMENTUM_120D_DAYS = 120

RELATIVE_STRENGTH_20D_DAYS = 20
RELATIVE_STRENGTH_60D_DAYS = 60

BREAKOUT_20D_DAYS = 20
BREAKOUT_60D_DAYS = 60

VOLUME_RECENT_DAYS = 5
VOLUME_BASELINE_DAYS = 20
VOLUME_TREND_HALF_WINDOW_DAYS = 10

VOLATILITY_20D_DAYS = 20
VOLATILITY_60D_DAYS = 60

RSI_PERIOD_DAYS = 14
ZSCORE_WINDOW_DAYS = 20

BENCHMARK_TICKERS = ("SPY", "QQQ")
CATEGORICAL_FEATURE_COLUMNS = ("Regime",)

# Numeric-only encoding for strategy signals, so they can feed numeric models
# alongside the continuous strategy scores.
SIGNAL_ENCODING = {"BUY": 1.0, "WAIT": 0.0, "AVOID": -1.0}

# ---------------------------------------------------------------------------
# Required history. This must cover every window above, the market-regime
# module's own requirement (also based on SPY/QQQ history), and whatever the
# most history-hungry *registered* strategy needs - so this constant
# automatically grows if a future strategy needs more history, with no
# manual bookkeeping here.
# ---------------------------------------------------------------------------
_PRICE_FEATURE_REQUIRED_HISTORY_DAYS = max(
    MOVING_AVERAGE_200D_DAYS,
    MOVING_AVERAGE_50D_DAYS + MA_SLOPE_LOOKBACK_DAYS,
    MOVING_AVERAGE_20D_DAYS + MA_SLOPE_LOOKBACK_DAYS,
    MOMENTUM_120D_DAYS + 1,
    BREAKOUT_60D_DAYS + 1,
    VOLATILITY_60D_DAYS + 1,
    RSI_PERIOD_DAYS + 1,
    ZSCORE_WINDOW_DAYS,
    VOLUME_BASELINE_DAYS + VOLUME_TREND_HALF_WINDOW_DAYS,
)


def _strategy_required_history_days():
    return max(
        (get_strategy(name).required_history_days for name in available_strategy_names()),
        default=0,
    )


REQUIRED_HISTORY_DAYS = max(
    _PRICE_FEATURE_REQUIRED_HISTORY_DAYS,
    REGIME_REQUIRED_HISTORY_DAYS,
    _strategy_required_history_days(),
)


class InsufficientHistoryError(ValueError):
    """Raised when a price/benchmark slice has fewer rows than required."""


def truncate_to_as_of(data, as_of_date):
    """Return only rows dated at or before `as_of_date` (point-in-time cut).

    This is the single explicit helper responsible for the "no future data"
    boundary. Every other function in this module and in `ml/dataset.py`
    should route price data through this before computing features, which
    makes the no-look-ahead rule easy to audit at a glance.
    """
    as_of_date = pd.Timestamp(as_of_date)
    return data.loc[data.index <= as_of_date]


def _moving_average(closes, window):
    return float(closes.tail(window).mean())


def _momentum(closes, days):
    """(current_close / close_N_days_ago) - 1."""
    current_price = closes.iloc[-1]
    old_price = closes.iloc[-(days + 1)]
    return float((current_price / old_price) - 1)


def _volatility(closes, days):
    """Standard deviation of daily % returns over the last `days` days."""
    daily_returns = closes.pct_change().dropna()
    return float(daily_returns.tail(days).std())


def _prior_high(closes, days):
    """Max close over the `days` rows STRICTLY BEFORE the current row.

    This excludes the current day's close from its own "prior high", which
    is what keeps breakout-style features look-ahead safe: today's price is
    only ever compared against a high that existed before today.
    """
    if len(closes) < days + 1:
        raise InsufficientHistoryError("Not enough rows for prior-high window.")
    window = closes.iloc[-(days + 1):-1]
    return float(window.max())


def _ma_slope(closes, window, slope_lookback):
    """Fractional change in a moving average over `slope_lookback` days."""
    ma_now = _moving_average(closes, window)
    prior_slice = closes.iloc[: len(closes) - slope_lookback]
    if len(prior_slice) < window:
        raise InsufficientHistoryError("Not enough rows for moving-average slope.")
    ma_prior = _moving_average(prior_slice, window)
    if ma_prior == 0:
        return np.nan
    return float((ma_now - ma_prior) / abs(ma_prior))


def _zscore(closes, window):
    """(price - mean) / population-std over the last `window` closes.

    Returns 0.0 for a perfectly flat window (std == 0): price, mean, and MA
    are then all equal, so a zero deviation is the correct, well-defined
    answer rather than an undefined 0/0.
    """
    values = closes.tail(window)
    mean = values.mean()
    std = values.std(ddof=0)
    if std == 0:
        return 0.0
    return float((values.iloc[-1] - mean) / std)


def _volume_ratio(volumes, recent_days, baseline_days):
    recent_average = volumes.tail(recent_days).mean()
    baseline_average = volumes.tail(baseline_days).mean()
    if not baseline_average or pd.isna(baseline_average):
        return np.nan
    return float(recent_average / baseline_average)


def _volume_trend(volumes, half_window):
    if len(volumes) < 2 * half_window:
        raise InsufficientHistoryError("Not enough rows for volume trend.")
    recent_half = volumes.tail(half_window).mean()
    prior_half = volumes.iloc[-(2 * half_window):-half_window].mean()
    if not prior_half or pd.isna(prior_half):
        return np.nan
    return float(recent_half / prior_half - 1)


def compute_price_trend_features(closes):
    """price, distance-from-MA{20,50,200}, and MA{20,50} slope."""
    price = float(closes.iloc[-1])
    ma20 = _moving_average(closes, MOVING_AVERAGE_20D_DAYS)
    ma50 = _moving_average(closes, MOVING_AVERAGE_50D_DAYS)
    ma200 = _moving_average(closes, MOVING_AVERAGE_200D_DAYS)
    return {
        "Price": price,
        "MA20": ma20,
        "MA50": ma50,
        "MA200": ma200,
        "Distance From MA20": (price / ma20) - 1,
        "Distance From MA50": (price / ma50) - 1,
        "Distance From MA200": (price / ma200) - 1,
        "MA20 Slope": _ma_slope(closes, MOVING_AVERAGE_20D_DAYS, MA_SLOPE_LOOKBACK_DAYS),
        "MA50 Slope": _ma_slope(closes, MOVING_AVERAGE_50D_DAYS, MA_SLOPE_LOOKBACK_DAYS),
    }


def compute_momentum_features(closes):
    return {
        "Momentum 5D": _momentum(closes, MOMENTUM_5D_DAYS),
        "Momentum 20D": _momentum(closes, MOMENTUM_20D_DAYS),
        "Momentum 60D": _momentum(closes, MOMENTUM_60D_DAYS),
        "Momentum 120D": _momentum(closes, MOMENTUM_120D_DAYS),
    }


def compute_relative_strength_features(stock_closes, spy_closes, qqq_closes):
    stock_20d = _momentum(stock_closes, RELATIVE_STRENGTH_20D_DAYS)
    stock_60d = _momentum(stock_closes, RELATIVE_STRENGTH_60D_DAYS)
    spy_20d = _momentum(spy_closes, RELATIVE_STRENGTH_20D_DAYS)
    spy_60d = _momentum(spy_closes, RELATIVE_STRENGTH_60D_DAYS)
    qqq_20d = _momentum(qqq_closes, RELATIVE_STRENGTH_20D_DAYS)
    qqq_60d = _momentum(qqq_closes, RELATIVE_STRENGTH_60D_DAYS)
    return {
        "Relative Strength 20D SPY": stock_20d - spy_20d,
        "Relative Strength 60D SPY": stock_60d - spy_60d,
        "Relative Strength 20D QQQ": stock_20d - qqq_20d,
        "Relative Strength 60D QQQ": stock_60d - qqq_60d,
    }


def compute_breakout_features(closes):
    price = float(closes.iloc[-1])
    prior_20d_high = _prior_high(closes, BREAKOUT_20D_DAYS)
    prior_60d_high = _prior_high(closes, BREAKOUT_60D_DAYS)
    return {
        "Breakout Distance 20D": (price / prior_20d_high) - 1,
        "Breakout Distance 60D": (price / prior_60d_high) - 1,
    }


def compute_volume_features(volumes):
    return {
        "Volume Ratio 5D 20D": _volume_ratio(
            volumes, VOLUME_RECENT_DAYS, VOLUME_BASELINE_DAYS
        ),
        "Volume Trend 20D": _volume_trend(volumes, VOLUME_TREND_HALF_WINDOW_DAYS),
    }


def compute_volatility_features(closes):
    return {
        "Volatility 20D": _volatility(closes, VOLATILITY_20D_DAYS),
        "Volatility 60D": _volatility(closes, VOLATILITY_60D_DAYS),
    }


def compute_mean_reversion_features(closes):
    return {
        "RSI14": calculate_rsi(closes, RSI_PERIOD_DAYS),
        "Price Zscore MA20": _zscore(closes, ZSCORE_WINDOW_DAYS),
    }


def compute_regime_features(spy_hist, qqq_hist, regime_result=None):
    """Categorical historical regime + supporting SPY/QQQ regime metrics.

    `regime_result` may be precomputed by the caller (e.g. once per
    evaluation date, reused across every ticker that date) to avoid
    recomputing the same SPY/QQQ features per ticker. If omitted, it is
    computed here directly from `spy_hist`/`qqq_hist`, which the caller must
    have already truncated to the same "as of" date as the ticker features.
    """
    if regime_result is None:
        regime_result = classify_market_regime(
            compute_market_features(spy_hist, qqq_hist)
        )
    features = regime_result.features
    return {
        "Regime": regime_result.regime,
        "Regime Confidence": float(regime_result.confidence),
        "Regime SPY Momentum 20D": features.spy_momentum_20d,
        "Regime SPY Momentum 60D": features.spy_momentum_60d,
        "Regime QQQ Momentum 20D": features.qqq_momentum_20d,
        "Regime QQQ Momentum 60D": features.qqq_momentum_60d,
        "Regime Market Volatility 20D": features.market_volatility_20d,
        "Regime SPY Above MA50": features.spy_price > features.spy_ma50,
        "Regime SPY Above MA200": features.spy_price > features.spy_ma200,
        "Regime QQQ Above MA50": features.qqq_price > features.qqq_ma50,
        "Regime QQQ Above MA200": features.qqq_price > features.qqq_ma200,
    }


def compute_strategy_features(ticker, historical_data, benchmark_data=None):
    """Score/signal outputs of every registered strategy, as extra features.

    IMPORTANT: this calls each strategy's own `analyze()` on the exact same
    point-in-time-truncated `historical_data` used for every other feature
    in this row, so strategy features obey the same no-look-ahead contract.
    Strategy logic itself is never duplicated here - only its outputs are
    read. If a strategy needs more history than is available, its
    `analyze()` returns None and both outputs are recorded as missing
    (NaN), to be handled by imputation downstream, rather than skipping the
    whole row.
    """
    strategy_features = {}
    for name in available_strategy_names():
        result = invoke_analyze(
            get_strategy(name).analyze,
            ticker,
            historical_data,
            benchmark_data=benchmark_data,
        )
        score_column = f"Strategy Score: {name}"
        signal_column = f"Strategy Signal: {name}"
        if result is None:
            strategy_features[score_column] = np.nan
            strategy_features[signal_column] = np.nan
            continue
        strategy_features[score_column] = float(result["Score"])
        strategy_features[signal_column] = SIGNAL_ENCODING.get(
            result["Signal"], np.nan
        )
    return strategy_features


def compute_feature_row(
    ticker,
    historical_data,
    spy_hist,
    qqq_hist,
    as_of_date=None,
    regime_result=None,
    include_strategy_features=True,
):
    """Build one point-in-time feature row for `ticker` as of its last row.

    `historical_data`, `spy_hist`, and `qqq_hist` must already be truncated
    (via `truncate_to_as_of`) to rows at or before the evaluation date; this
    function re-truncates defensively if `as_of_date` is given, but does not
    otherwise inspect wall-clock time or any external state.

    Raises `InsufficientHistoryError` if there are fewer than
    `REQUIRED_HISTORY_DAYS` valid rows for the ticker.
    """
    if as_of_date is not None:
        historical_data = truncate_to_as_of(historical_data, as_of_date)
        spy_hist = truncate_to_as_of(spy_hist, as_of_date)
        qqq_hist = truncate_to_as_of(qqq_hist, as_of_date)

    historical_data = historical_data.dropna(subset=["Close"])
    if len(historical_data) < REQUIRED_HISTORY_DAYS:
        raise InsufficientHistoryError(
            f"{ticker}: need at least {REQUIRED_HISTORY_DAYS} rows, "
            f"got {len(historical_data)}."
        )

    closes = historical_data["Close"]
    volumes = historical_data["Volume"] if "Volume" in historical_data.columns else None
    spy_closes = spy_hist["Close"].dropna()
    qqq_closes = qqq_hist["Close"].dropna()

    row = {}
    row.update(compute_price_trend_features(closes))
    row.update(compute_momentum_features(closes))
    row.update(compute_relative_strength_features(closes, spy_closes, qqq_closes))
    row.update(compute_breakout_features(closes))
    if volumes is not None:
        row.update(compute_volume_features(volumes))
    row.update(compute_volatility_features(closes))
    row.update(compute_mean_reversion_features(closes))
    row.update(compute_regime_features(spy_hist, qqq_hist, regime_result=regime_result))
    if include_strategy_features:
        row.update(compute_strategy_features(ticker, historical_data, benchmark_data=qqq_hist))
    return row


def numeric_feature_columns():
    """Best-effort static list of purely numeric feature column names.

    Useful for documentation/tests; `ml.models.infer_feature_columns` is the
    authoritative, dataframe-driven way to split numeric vs. categorical
    columns once strategy features (whose names depend on the registry) are
    included.
    """
    return [
        "Price",
        "MA20",
        "MA50",
        "MA200",
        "Distance From MA20",
        "Distance From MA50",
        "Distance From MA200",
        "MA20 Slope",
        "MA50 Slope",
        "Momentum 5D",
        "Momentum 20D",
        "Momentum 60D",
        "Momentum 120D",
        "Relative Strength 20D SPY",
        "Relative Strength 60D SPY",
        "Relative Strength 20D QQQ",
        "Relative Strength 60D QQQ",
        "Breakout Distance 20D",
        "Breakout Distance 60D",
        "Volume Ratio 5D 20D",
        "Volume Trend 20D",
        "Volatility 20D",
        "Volatility 60D",
        "RSI14",
        "Price Zscore MA20",
        "Regime Confidence",
        "Regime SPY Momentum 20D",
        "Regime SPY Momentum 60D",
        "Regime QQQ Momentum 20D",
        "Regime QQQ Momentum 60D",
        "Regime Market Volatility 20D",
        "Regime SPY Above MA50",
        "Regime SPY Above MA200",
        "Regime QQQ Above MA50",
        "Regime QQQ Above MA200",
    ]
