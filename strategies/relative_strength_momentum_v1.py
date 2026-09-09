"""Relative Strength Momentum V1: stock momentum plus outperformance vs a
benchmark.

This strategy is intentionally simple and has NOT been tuned against
backtest results. It favors names that are rising AND beating the
benchmark (QQQ by default) over the same lookbacks.

Benchmark history is passed in by the data/backtest layer as
`benchmark_data`. This module never downloads prices itself.

It does not use RSI, MACD, news, Kalman filters, machine learning, sector
rotation, or regime detection - those are explicitly deferred.

All tunable constants live at the top of this file so they are easy to
find and adjust later.
"""

import pandas as pd


# ---------------------------------------------------------------------------
# Lookback windows (in trading days)
# ---------------------------------------------------------------------------
MOMENTUM_20D_DAYS = 20
MOMENTUM_60D_DAYS = 60
MOMENTUM_120D_DAYS = 120
MOVING_AVERAGE_50D_DAYS = 50
VOLATILITY_LOOKBACK_DAYS = 20

# Preferred relative-strength series. Callers load this through
# load_market_data / the backtest download path - never from this file.
DEFAULT_BENCHMARK_TICKER = "QQQ"

# ---------------------------------------------------------------------------
# Scoring weights. These sum to 1.0 across the six raw-score inputs.
# ---------------------------------------------------------------------------
WEIGHT_MOMENTUM_20D = 0.20
WEIGHT_MOMENTUM_60D = 0.20
WEIGHT_MOMENTUM_120D = 0.10
WEIGHT_RELATIVE_STRENGTH_20D = 0.20
WEIGHT_RELATIVE_STRENGTH_60D = 0.20
WEIGHT_TREND = 0.10

# ---------------------------------------------------------------------------
# Volatility penalty. Modest: same multiplier family as Momentum V2.
#
#     volatility_penalty = VOLATILITY_PENALTY_MULTIPLIER * volatility_20d
#
# Subtracted from the raw score so extreme instability ranks lower without
# eliminating volatile momentum names outright.
# ---------------------------------------------------------------------------
VOLATILITY_PENALTY_MULTIPLIER = 0.5

# ---------------------------------------------------------------------------
# Required historical rows. 120-day stock momentum needs the current row plus
# a close 120 trading rows earlier (121 rows). Relative-strength lookbacks
# are 20D and 60D, so 121 also covers the benchmark series.
# ---------------------------------------------------------------------------
REQUIRED_HISTORY_DAYS = max(
    MOMENTUM_20D_DAYS + 1,
    MOMENTUM_60D_DAYS + 1,
    MOMENTUM_120D_DAYS + 1,
    MOVING_AVERAGE_50D_DAYS,
    VOLATILITY_LOOKBACK_DAYS + 1,
)


def calculate_momentum(closing_prices, days):
    """Return (current_close / close_N_days_ago) - 1."""
    if len(closing_prices) < days + 1:
        raise ValueError("Not enough historical data.")
    current_price = closing_prices.iloc[-1]
    old_price = closing_prices.iloc[-(days + 1)]
    return (current_price / old_price) - 1


def calculate_moving_average(closing_prices, days):
    """Return the simple moving average of the last `days` closes."""
    if len(closing_prices) < days:
        raise ValueError("Not enough historical data.")
    return closing_prices.tail(days).mean()


def calculate_volatility(closing_prices, days):
    """Return the standard deviation of daily % returns over the last `days`."""
    if len(closing_prices) < days + 1:
        raise ValueError("Not enough historical data.")
    daily_returns = closing_prices.pct_change().dropna()
    return daily_returns.tail(days).std()


def calculate_relative_strength(stock_momentum, benchmark_momentum):
    """Stock momentum minus benchmark momentum over the same lookback."""
    return stock_momentum - benchmark_momentum


def generate_signal(momentum_20d, momentum_60d, relative_strength_60d, above_ma50):
    """BUY only when stock trend and 60D outperformance agree.

    AVOID for clearly weak/underperforming conditions. WAIT otherwise -
    BUY is never forced on mixed evidence.
    """
    if (
        momentum_20d > 0
        and momentum_60d > 0
        and relative_strength_60d > 0
        and above_ma50
    ):
        return "BUY"
    if (
        momentum_20d < 0
        and momentum_60d < 0
        and relative_strength_60d < 0
        and not above_ma50
    ):
        return "AVOID"
    return "WAIT"


def _describe(value):
    if value > 0:
        return "positive"
    if value < 0:
        return "negative"
    return "flat"


def _build_reason(
    signal, momentum_20d, momentum_60d, relative_strength_60d, above_ma50
):
    momentum_phrase = (
        f"{_describe(momentum_20d)} 20D and {_describe(momentum_60d)} 60D momentum"
    )
    rs_phrase = f"{_describe(relative_strength_60d)} 60D relative strength"
    trend_phrase = "price above MA50" if above_ma50 else "price at/below MA50"
    base = f"{momentum_phrase.capitalize()}, {rs_phrase}, {trend_phrase}"

    if signal == "BUY":
        return (
            f"{base}; ranked using stock momentum and relative strength with a "
            "modest volatility penalty."
        )
    if signal == "AVOID":
        return f"{base}; clearly weak versus its own trend and the benchmark."
    return f"{base}; mixed evidence, held at WAIT."


def analyze(ticker, data, benchmark_data=None):
    """Return the common strategy result for Relative Strength Momentum V1.

    `benchmark_data` must be an OHLCV frame supplied by the caller. This
    function does not fetch it.
    """
    if benchmark_data is None:
        return None

    data = data.dropna(subset=["Close"])
    benchmark = benchmark_data.dropna(subset=["Close"])
    if len(data) < REQUIRED_HISTORY_DAYS or len(benchmark) < REQUIRED_HISTORY_DAYS:
        return None

    closing_prices = data["Close"]
    benchmark_closes = benchmark["Close"]

    try:
        current_price = float(closing_prices.iloc[-1])
        momentum_20d = calculate_momentum(closing_prices, MOMENTUM_20D_DAYS)
        momentum_60d = calculate_momentum(closing_prices, MOMENTUM_60D_DAYS)
        momentum_120d = calculate_momentum(closing_prices, MOMENTUM_120D_DAYS)
        benchmark_momentum_20d = calculate_momentum(
            benchmark_closes, MOMENTUM_20D_DAYS
        )
        benchmark_momentum_60d = calculate_momentum(
            benchmark_closes, MOMENTUM_60D_DAYS
        )
        moving_average_50d = calculate_moving_average(
            closing_prices, MOVING_AVERAGE_50D_DAYS
        )
        volatility_20d = calculate_volatility(closing_prices, VOLATILITY_LOOKBACK_DAYS)
    except (KeyError, ValueError):
        return None

    if pd.isna(volatility_20d):
        return None

    relative_strength_20d = calculate_relative_strength(
        momentum_20d, benchmark_momentum_20d
    )
    relative_strength_60d = calculate_relative_strength(
        momentum_60d, benchmark_momentum_60d
    )
    above_ma50 = current_price > moving_average_50d
    trend_component = 1.0 if above_ma50 else 0.0

    raw_score = (
        WEIGHT_MOMENTUM_20D * momentum_20d
        + WEIGHT_MOMENTUM_60D * momentum_60d
        + WEIGHT_MOMENTUM_120D * momentum_120d
        + WEIGHT_RELATIVE_STRENGTH_20D * relative_strength_20d
        + WEIGHT_RELATIVE_STRENGTH_60D * relative_strength_60d
        + WEIGHT_TREND * trend_component
    )
    volatility_penalty = VOLATILITY_PENALTY_MULTIPLIER * volatility_20d
    final_score = raw_score - volatility_penalty

    signal = generate_signal(
        momentum_20d, momentum_60d, relative_strength_60d, above_ma50
    )
    reason = _build_reason(
        signal, momentum_20d, momentum_60d, relative_strength_60d, above_ma50
    )

    factor_details = {
        "Price": current_price,
        "Momentum 20D": momentum_20d,
        "Momentum 60D": momentum_60d,
        "Momentum 120D": momentum_120d,
        "Benchmark Momentum 20D": benchmark_momentum_20d,
        "Benchmark Momentum 60D": benchmark_momentum_60d,
        "Relative Strength 20D": relative_strength_20d,
        "Relative Strength 60D": relative_strength_60d,
        "MA50": moving_average_50d,
        "Above MA50": above_ma50,
        "Volatility 20D": volatility_20d,
        "Raw Score": raw_score,
        "Volatility Penalty": volatility_penalty,
        "Final Score": final_score,
    }
    return {
        "Ticker": ticker,
        "Score": final_score,
        "Signal": signal,
        "Reason": reason,
        "Factor Details": factor_details,
        **factor_details,
    }


def rank_key(result):
    """Higher combined score ranks first; 60D relative strength breaks ties."""
    return (result["Score"], result["Relative Strength 60D"])


PARAMETERS = {
    "momentum_20d_days": MOMENTUM_20D_DAYS,
    "momentum_60d_days": MOMENTUM_60D_DAYS,
    "momentum_120d_days": MOMENTUM_120D_DAYS,
    "moving_average_50d_days": MOVING_AVERAGE_50D_DAYS,
    "volatility_lookback_days": VOLATILITY_LOOKBACK_DAYS,
    "weight_momentum_20d": WEIGHT_MOMENTUM_20D,
    "weight_momentum_60d": WEIGHT_MOMENTUM_60D,
    "weight_momentum_120d": WEIGHT_MOMENTUM_120D,
    "weight_relative_strength_20d": WEIGHT_RELATIVE_STRENGTH_20D,
    "weight_relative_strength_60d": WEIGHT_RELATIVE_STRENGTH_60D,
    "weight_trend": WEIGHT_TREND,
    "volatility_penalty_multiplier": VOLATILITY_PENALTY_MULTIPLIER,
    "default_benchmark_ticker": DEFAULT_BENCHMARK_TICKER,
}
