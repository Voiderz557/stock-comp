"""Aggressive Momentum V1: a competition-oriented momentum strategy.

This strategy is intentionally simple and has NOT been tuned against
backtest results. Compared with Momentum V2 it:

- puts more weight on recent (20-day) momentum,
- adds a 20-day breakout-strength factor,
- adds a modest volume-confirmation factor, and
- applies a SMALLER volatility penalty, because this strategy is meant to
  tolerate more volatility than Momentum V2. The penalty only exists to
  stop one extremely unstable stock from automatically dominating the
  rankings from a single explosive move.

It does not use RSI, MACD, Bollinger Bands, machine learning, fundamentals,
earnings data, sentiment, news, Kalman filters, or regime detection - those
are explicitly deferred.

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
BREAKOUT_LOOKBACK_DAYS = 20
VOLUME_RECENT_DAYS = 5
VOLUME_BASELINE_DAYS = 20
VOLATILITY_LOOKBACK_DAYS = 20

# ---------------------------------------------------------------------------
# Scoring weights. These sum to 1.0 across the five raw-score inputs.
# ---------------------------------------------------------------------------
WEIGHT_MOMENTUM_20D = 0.30
WEIGHT_MOMENTUM_60D = 0.30
WEIGHT_MOMENTUM_120D = 0.15
WEIGHT_BREAKOUT = 0.15
WEIGHT_VOLUME = 0.10

# ---------------------------------------------------------------------------
# Volatility penalty - intentionally SMALLER than Momentum V2's 0.5
# multiplier (strategies/momentum_v2.py::VOLATILITY_PENALTY_MULTIPLIER).
# Aggressive Momentum V1 is meant to tolerate more volatility than Momentum
# V2, so instability is discouraged less strongly here.
#
#     volatility_penalty = VOLATILITY_PENALTY_MULTIPLIER * volatility_20d
#
# The penalty is subtracted from the raw score. It is modest: it reduces
# the rank of an unstable name without eliminating volatile momentum names.
# ---------------------------------------------------------------------------
VOLATILITY_PENALTY_MULTIPLIER = 0.25

# Volume confirmation is (recent_avg / baseline_avg) - 1, then clamped so
# one abnormal spike or drought cannot dominate the combined score. With
# WEIGHT_VOLUME = 0.10 the maximum volume contribution is +/- 0.10.
VOLUME_CONFIRMATION_CLAMP = 1.0

# BUY requires the close to sit in the upper portion of its 20-day range.
# 0.70 means "at least 70% of the way from the 20-day low to the 20-day
# high" - near the high, but not so strict that only exact highs qualify.
BREAKOUT_BUY_THRESHOLD = 0.70

# ---------------------------------------------------------------------------
# Required historical rows. The 120-day momentum factor needs the current
# row plus a close price 120 trading rows earlier, i.e. at least 121 rows.
# That comfortably covers every other factor (61 rows for 60-day
# momentum, 50 for MA50, 21 for 20-day momentum/volatility, 20 for the
# breakout window and volume baseline).
# ---------------------------------------------------------------------------
REQUIRED_HISTORY_DAYS = max(
    MOMENTUM_20D_DAYS + 1,
    MOMENTUM_60D_DAYS + 1,
    MOMENTUM_120D_DAYS + 1,
    MOVING_AVERAGE_50D_DAYS,
    BREAKOUT_LOOKBACK_DAYS,
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


def calculate_breakout_strength(closing_prices, days):
    """How close the current close is to the recent N-day high.

    Measured as the close's position in its own N-day high/low range:

        (current_close - N-day_low) / (N-day_high - N-day_low)

    1.0 means the close is at the N-day high; 0.0 means it is at the
    N-day low. A flat window (high == low) returns 1.0 because the close
    is at that high.

    Higher values increase the combined score.
    """
    if len(closing_prices) < days:
        raise ValueError("Not enough historical data.")
    window = closing_prices.tail(days)
    window_high = window.max()
    window_low = window.min()
    window_range = window_high - window_low
    current_price = closing_prices.iloc[-1]
    if window_range <= 0:
        return 1.0
    return (current_price - window_low) / window_range


def calculate_volume_confirmation(volumes, recent_days, baseline_days, clamp):
    """Recent average volume vs. its own longer-run average, centered at 0.

        volume_confirmation = (recent_avg / baseline_avg) - 1

    Positive means recent volume is running above its own baseline
    (confirming a move); negative means volume is drying up. The result is
    clamped so volume cannot dominate the combined score.
    """
    if len(volumes) < baseline_days:
        raise ValueError("Not enough historical data.")
    baseline_average = float(volumes.tail(baseline_days).mean())
    recent_average = float(volumes.tail(recent_days).mean())
    if baseline_average <= 0:
        return recent_average, baseline_average, 0.0
    ratio = (recent_average / baseline_average) - 1
    confirmation = max(-clamp, min(clamp, ratio))
    return recent_average, baseline_average, confirmation


def calculate_volatility(closing_prices, days):
    """Return the standard deviation of daily % returns over the last `days`."""
    if len(closing_prices) < days + 1:
        raise ValueError("Not enough historical data.")
    daily_returns = closing_prices.pct_change().dropna()
    return daily_returns.tail(days).std()


def generate_signal(momentum_20d, momentum_60d, above_ma50, breakout_strength):
    """BUY only when recent momentum, trend, and breakout all agree.

    AVOID for clearly bearish conditions. WAIT otherwise - BUY is never
    forced when evidence is mixed or incomplete.
    """
    if (
        momentum_20d > 0
        and momentum_60d > 0
        and above_ma50
        and breakout_strength >= BREAKOUT_BUY_THRESHOLD
    ):
        return "BUY"
    if momentum_20d < 0 and momentum_60d < 0 and not above_ma50:
        return "AVOID"
    return "WAIT"


def _describe(value):
    if value > 0:
        return "positive"
    if value < 0:
        return "negative"
    return "flat"


def _build_reason(signal, momentum_20d, momentum_60d, above_ma50, breakout_strength):
    momentum_phrase = (
        f"{_describe(momentum_20d)} 20D and {_describe(momentum_60d)} 60D momentum"
    )
    trend_phrase = "price above MA50" if above_ma50 else "price at/below MA50"
    if breakout_strength >= BREAKOUT_BUY_THRESHOLD:
        breakout_phrase = "reasonable 20D breakout strength"
    else:
        breakout_phrase = "weak 20D breakout strength"
    base = f"{momentum_phrase.capitalize()}, {trend_phrase}, {breakout_phrase}"

    if signal == "BUY":
        return (
            f"{base}; ranked using multi-horizon momentum, breakout, and "
            "volume confirmation with a modest volatility penalty."
        )
    if signal == "AVOID":
        return f"{base}; clearly bearish, does not meet buy conditions."
    return f"{base}; mixed evidence, held at WAIT."


def analyze(ticker, data):
    """Return the common strategy result for Aggressive Momentum V1."""
    data = data.dropna(subset=["Close", "Volume"])
    if len(data) < REQUIRED_HISTORY_DAYS:
        return None

    closing_prices = data["Close"]
    volumes = data["Volume"]

    try:
        current_price = float(closing_prices.iloc[-1])
        momentum_20d = calculate_momentum(closing_prices, MOMENTUM_20D_DAYS)
        momentum_60d = calculate_momentum(closing_prices, MOMENTUM_60D_DAYS)
        momentum_120d = calculate_momentum(closing_prices, MOMENTUM_120D_DAYS)
        moving_average_50d = calculate_moving_average(
            closing_prices, MOVING_AVERAGE_50D_DAYS
        )
        breakout_strength = calculate_breakout_strength(
            closing_prices, BREAKOUT_LOOKBACK_DAYS
        )
        recent_volume_average, volume_baseline_average, volume_confirmation = (
            calculate_volume_confirmation(
                volumes,
                VOLUME_RECENT_DAYS,
                VOLUME_BASELINE_DAYS,
                VOLUME_CONFIRMATION_CLAMP,
            )
        )
        volatility_20d = calculate_volatility(closing_prices, VOLATILITY_LOOKBACK_DAYS)
    except (KeyError, ValueError):
        return None

    if pd.isna(volatility_20d):
        return None

    above_ma50 = current_price > moving_average_50d

    raw_score = (
        WEIGHT_MOMENTUM_20D * momentum_20d
        + WEIGHT_MOMENTUM_60D * momentum_60d
        + WEIGHT_MOMENTUM_120D * momentum_120d
        + WEIGHT_BREAKOUT * breakout_strength
        + WEIGHT_VOLUME * volume_confirmation
    )
    volatility_penalty = VOLATILITY_PENALTY_MULTIPLIER * volatility_20d
    final_score = raw_score - volatility_penalty

    signal = generate_signal(
        momentum_20d, momentum_60d, above_ma50, breakout_strength
    )
    reason = _build_reason(
        signal, momentum_20d, momentum_60d, above_ma50, breakout_strength
    )

    factor_details = {
        "Price": current_price,
        "Momentum 20D": momentum_20d,
        "Momentum 60D": momentum_60d,
        "Momentum 120D": momentum_120d,
        "MA50": moving_average_50d,
        "Above MA50": above_ma50,
        "Breakout Strength": breakout_strength,
        "Recent Volume Average": recent_volume_average,
        "Volume Baseline Average": volume_baseline_average,
        "Volume Confirmation": volume_confirmation,
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
        # Compatibility fields for any display that flattens factor details.
        **factor_details,
    }


def rank_key(result):
    """Higher combined score ranks first; 120D momentum breaks close ties."""
    return (result["Score"], result["Momentum 120D"])


PARAMETERS = {
    "momentum_20d_days": MOMENTUM_20D_DAYS,
    "momentum_60d_days": MOMENTUM_60D_DAYS,
    "momentum_120d_days": MOMENTUM_120D_DAYS,
    "moving_average_50d_days": MOVING_AVERAGE_50D_DAYS,
    "breakout_lookback_days": BREAKOUT_LOOKBACK_DAYS,
    "volume_recent_days": VOLUME_RECENT_DAYS,
    "volume_baseline_days": VOLUME_BASELINE_DAYS,
    "volatility_lookback_days": VOLATILITY_LOOKBACK_DAYS,
    "weight_momentum_20d": WEIGHT_MOMENTUM_20D,
    "weight_momentum_60d": WEIGHT_MOMENTUM_60D,
    "weight_momentum_120d": WEIGHT_MOMENTUM_120D,
    "weight_breakout": WEIGHT_BREAKOUT,
    "weight_volume": WEIGHT_VOLUME,
    "volatility_penalty_multiplier": VOLATILITY_PENALTY_MULTIPLIER,
    "volume_confirmation_clamp": VOLUME_CONFIRMATION_CLAMP,
    "breakout_buy_threshold": BREAKOUT_BUY_THRESHOLD,
}
