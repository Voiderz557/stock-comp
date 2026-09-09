"""Breakout Volume V1: prior-high breakouts with volume confirmation.

This strategy is intentionally simple and has NOT been tuned against
backtest results. It looks for names pushing through a recent high, with
enough volume that the move is not an empty spike.

Prior highs EXCLUDE the current row so a new high is measured against
history that was already known, which keeps the factor look-ahead safe.

It does not use RSI, MACD, news, Kalman filters, machine learning, sector
rotation, or regime detection - those are explicitly deferred.

All tunable constants live at the top of this file so they are easy to
find and adjust later.
"""

import pandas as pd


# ---------------------------------------------------------------------------
# Lookback windows (in trading days)
# ---------------------------------------------------------------------------
BREAKOUT_20D_DAYS = 20
BREAKOUT_60D_DAYS = 60
MOMENTUM_20D_DAYS = 20
MOMENTUM_60D_DAYS = 60
MOVING_AVERAGE_50D_DAYS = 50
VOLUME_RECENT_DAYS = 5
VOLUME_BASELINE_DAYS = 20
VOLATILITY_LOOKBACK_DAYS = 20

# ---------------------------------------------------------------------------
# Scoring weights. These sum to 1.0 across the six raw-score inputs.
# ---------------------------------------------------------------------------
WEIGHT_BREAKOUT_20D = 0.25
WEIGHT_BREAKOUT_60D = 0.20
WEIGHT_MOMENTUM_20D = 0.20
WEIGHT_MOMENTUM_60D = 0.15
WEIGHT_VOLUME = 0.10
WEIGHT_TREND = 0.10

# ---------------------------------------------------------------------------
# Volatility penalty. Modest, same family as Momentum V2.
#
#     volatility_penalty = VOLATILITY_PENALTY_MULTIPLIER * volatility_20d
# ---------------------------------------------------------------------------
VOLATILITY_PENALTY_MULTIPLIER = 0.5

# Volume contribution is (volume_ratio - 1), clamped so a single extreme
# spike cannot dominate. With WEIGHT_VOLUME = 0.10 the volume term is at
# most +/- 0.10.
VOLUME_CONFIRMATION_CLAMP = 1.0

# BUY: close is at/above the prior 20-day high, or within 2% of it.
BREAKOUT_BUY_THRESHOLD = -0.02

# BUY: recent volume is not clearly weak versus the 20-day baseline.
VOLUME_BUY_MIN_RATIO = 0.80

# ---------------------------------------------------------------------------
# Required historical rows. Prior 60-day high and 60-day momentum both need
# the current row plus 60 prior rows (61). That also covers MA50, 20-day
# breakout/momentum/volume, and 20-day volatility.
# ---------------------------------------------------------------------------
REQUIRED_HISTORY_DAYS = max(
    BREAKOUT_20D_DAYS + 1,
    BREAKOUT_60D_DAYS + 1,
    MOMENTUM_20D_DAYS + 1,
    MOMENTUM_60D_DAYS + 1,
    MOVING_AVERAGE_50D_DAYS,
    VOLUME_BASELINE_DAYS,
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


def calculate_prior_high(closing_prices, days):
    """Maximum close over the previous `days` rows, excluding the current row.

    Example for 20 days: max(Close[-21:-1]), never Close[-1].
    """
    if len(closing_prices) < days + 1:
        raise ValueError("Not enough historical data.")
    prior_window = closing_prices.iloc[-(days + 1) : -1]
    prior_high = float(prior_window.max())
    if prior_high <= 0:
        raise ValueError("Prior high must be positive.")
    return prior_high


def calculate_breakout(current_price, prior_high):
    """(current_price / prior_high) - 1. Positive means a breakout."""
    return (current_price / prior_high) - 1


def calculate_volume_ratio(volumes, recent_days, baseline_days):
    """Recent 5-day average volume vs 20-day average volume."""
    if len(volumes) < baseline_days:
        raise ValueError("Not enough historical data.")
    recent_average = float(volumes.tail(recent_days).mean())
    baseline_average = float(volumes.tail(baseline_days).mean())
    if baseline_average <= 0:
        return recent_average, baseline_average, 0.0
    return recent_average, baseline_average, recent_average / baseline_average


def volume_confirmation_from_ratio(volume_ratio, clamp):
    """Centered, clamped volume term used in the combined score."""
    return max(-clamp, min(clamp, volume_ratio - 1.0))


def calculate_volatility(closing_prices, days):
    """Return the standard deviation of daily % returns over the last `days`."""
    if len(closing_prices) < days + 1:
        raise ValueError("Not enough historical data.")
    daily_returns = closing_prices.pct_change().dropna()
    return daily_returns.tail(days).std()


def generate_signal(
    breakout_20d, momentum_20d, momentum_60d, above_ma50, volume_ratio
):
    """BUY only when a near-high breakout, trend, and volume agree.

    AVOID for clearly bearish conditions. WAIT otherwise - BUY is never
    forced on mixed evidence.
    """
    if (
        breakout_20d >= BREAKOUT_BUY_THRESHOLD
        and momentum_20d > 0
        and momentum_60d > 0
        and above_ma50
        and volume_ratio >= VOLUME_BUY_MIN_RATIO
    ):
        return "BUY"
    if (
        breakout_20d < 0
        and momentum_20d < 0
        and momentum_60d < 0
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
    signal, breakout_20d, momentum_20d, momentum_60d, above_ma50, volume_ratio
):
    if breakout_20d >= BREAKOUT_BUY_THRESHOLD:
        breakout_phrase = "near or above the prior 20D high"
    else:
        breakout_phrase = "below the prior 20D high"
    momentum_phrase = (
        f"{_describe(momentum_20d)} 20D and {_describe(momentum_60d)} 60D momentum"
    )
    trend_phrase = "price above MA50" if above_ma50 else "price at/below MA50"
    if volume_ratio >= VOLUME_BUY_MIN_RATIO:
        volume_phrase = "volume not clearly weak"
    else:
        volume_phrase = "weak volume confirmation"
    base = (
        f"{breakout_phrase.capitalize()}, {momentum_phrase}, {trend_phrase}, "
        f"{volume_phrase}"
    )

    if signal == "BUY":
        return (
            f"{base}; ranked using prior-high breakouts and volume confirmation "
            "with a modest volatility penalty."
        )
    if signal == "AVOID":
        return f"{base}; clearly bearish, does not meet buy conditions."
    return f"{base}; mixed evidence, held at WAIT."


def analyze(ticker, data):
    """Return the common strategy result for Breakout Volume V1."""
    data = data.dropna(subset=["Close", "Volume"])
    if len(data) < REQUIRED_HISTORY_DAYS:
        return None

    closing_prices = data["Close"]
    volumes = data["Volume"]

    try:
        current_price = float(closing_prices.iloc[-1])
        prior_20d_high = calculate_prior_high(closing_prices, BREAKOUT_20D_DAYS)
        prior_60d_high = calculate_prior_high(closing_prices, BREAKOUT_60D_DAYS)
        breakout_20d = calculate_breakout(current_price, prior_20d_high)
        breakout_60d = calculate_breakout(current_price, prior_60d_high)
        momentum_20d = calculate_momentum(closing_prices, MOMENTUM_20D_DAYS)
        momentum_60d = calculate_momentum(closing_prices, MOMENTUM_60D_DAYS)
        moving_average_50d = calculate_moving_average(
            closing_prices, MOVING_AVERAGE_50D_DAYS
        )
        recent_volume, average_20d_volume, volume_ratio = calculate_volume_ratio(
            volumes, VOLUME_RECENT_DAYS, VOLUME_BASELINE_DAYS
        )
        volatility_20d = calculate_volatility(closing_prices, VOLATILITY_LOOKBACK_DAYS)
    except (KeyError, ValueError):
        return None

    if pd.isna(volatility_20d):
        return None

    above_ma50 = current_price > moving_average_50d
    trend_component = 1.0 if above_ma50 else 0.0
    volume_confirmation = volume_confirmation_from_ratio(
        volume_ratio, VOLUME_CONFIRMATION_CLAMP
    )

    raw_score = (
        WEIGHT_BREAKOUT_20D * breakout_20d
        + WEIGHT_BREAKOUT_60D * breakout_60d
        + WEIGHT_MOMENTUM_20D * momentum_20d
        + WEIGHT_MOMENTUM_60D * momentum_60d
        + WEIGHT_VOLUME * volume_confirmation
        + WEIGHT_TREND * trend_component
    )
    volatility_penalty = VOLATILITY_PENALTY_MULTIPLIER * volatility_20d
    final_score = raw_score - volatility_penalty

    signal = generate_signal(
        breakout_20d, momentum_20d, momentum_60d, above_ma50, volume_ratio
    )
    reason = _build_reason(
        signal, breakout_20d, momentum_20d, momentum_60d, above_ma50, volume_ratio
    )

    factor_details = {
        "Price": current_price,
        "Prior 20D High": prior_20d_high,
        "Prior 60D High": prior_60d_high,
        "Breakout 20D": breakout_20d,
        "Breakout 60D": breakout_60d,
        "Momentum 20D": momentum_20d,
        "Momentum 60D": momentum_60d,
        "MA50": moving_average_50d,
        "Above MA50": above_ma50,
        "Recent 5D Volume": recent_volume,
        "Average 20D Volume": average_20d_volume,
        "Volume Ratio": volume_ratio,
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
    """Higher combined score ranks first; 20D breakout breaks close ties."""
    return (result["Score"], result["Breakout 20D"])


PARAMETERS = {
    "breakout_20d_days": BREAKOUT_20D_DAYS,
    "breakout_60d_days": BREAKOUT_60D_DAYS,
    "momentum_20d_days": MOMENTUM_20D_DAYS,
    "momentum_60d_days": MOMENTUM_60D_DAYS,
    "moving_average_50d_days": MOVING_AVERAGE_50D_DAYS,
    "volume_recent_days": VOLUME_RECENT_DAYS,
    "volume_baseline_days": VOLUME_BASELINE_DAYS,
    "volatility_lookback_days": VOLATILITY_LOOKBACK_DAYS,
    "weight_breakout_20d": WEIGHT_BREAKOUT_20D,
    "weight_breakout_60d": WEIGHT_BREAKOUT_60D,
    "weight_momentum_20d": WEIGHT_MOMENTUM_20D,
    "weight_momentum_60d": WEIGHT_MOMENTUM_60D,
    "weight_volume": WEIGHT_VOLUME,
    "weight_trend": WEIGHT_TREND,
    "volatility_penalty_multiplier": VOLATILITY_PENALTY_MULTIPLIER,
    "volume_confirmation_clamp": VOLUME_CONFIRMATION_CLAMP,
    "breakout_buy_threshold": BREAKOUT_BUY_THRESHOLD,
    "volume_buy_min_ratio": VOLUME_BUY_MIN_RATIO,
}
