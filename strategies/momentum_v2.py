"""Momentum V2 (Version 1): a multi-horizon momentum strategy with a modest
volatility penalty.

This strategy is intentionally simple. It combines three momentum horizons
and a trend filter into one continuous score, then applies a small penalty
for recent volatility so that one extremely unstable stock cannot dominate
purely because of a single explosive move. It does not use RSI, MACD,
Bollinger Bands, machine learning, fundamentals, earnings data, sentiment, or
regime detection - those are explicitly deferred to a later version.

All tunable constants live at the top of this file so they are easy to find
and adjust later. None of these weights have been tuned against backtest
results; they are a readable starting point only.
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

# ---------------------------------------------------------------------------
# Scoring weights. These must sum to 1.0 across the four raw-score inputs.
# Higher weight = larger influence on the combined score before the
# volatility penalty is applied.
# ---------------------------------------------------------------------------
WEIGHT_MOMENTUM_20D = 0.20
WEIGHT_MOMENTUM_60D = 0.30
WEIGHT_MOMENTUM_120D = 0.30
WEIGHT_TREND = 0.20

# ---------------------------------------------------------------------------
# Volatility penalty.
#
# `volatility_20d` is the standard deviation of daily percentage returns over
# the last VOLATILITY_LOOKBACK_DAYS trading days - a fractional value on the
# same rough order of magnitude as the momentum inputs above (a few percent
# for a calm stock, more for an unstable one).
#
# The penalty subtracted from the raw score is:
#
#     volatility_penalty = VOLATILITY_PENALTY_MULTIPLIER * volatility_20d
#
# A multiplier of 0.5 keeps the penalty modest: it reduces the score of a
# volatile stock without being large enough to eliminate volatile stocks
# outright. This is a momentum strategy, so some volatility is expected and
# acceptable - the penalty only discourages extreme instability.
# ---------------------------------------------------------------------------
VOLATILITY_PENALTY_MULTIPLIER = 0.5

# ---------------------------------------------------------------------------
# Required historical rows.
#
# The 120-day momentum factor needs the current row plus a close price 120
# trading rows earlier, i.e. at least 121 rows. This is also comfortably more
# than every other factor needs (61 rows for 60-day momentum, 50 rows for the
# moving average, 21 rows for 20-day momentum/volatility), so it covers all
# of them.
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


def _describe_momentum(value):
    if value > 0:
        return "positive"
    if value < 0:
        return "negative"
    return "flat"


def _build_reason(signal, momentum_60d, momentum_120d, above_ma50):
    momentum_phrase = (
        f"{_describe_momentum(momentum_60d)} 60D and "
        f"{_describe_momentum(momentum_120d)} 120D momentum"
    )
    trend_phrase = "price above MA50" if above_ma50 else "price at/below MA50"
    base = f"{momentum_phrase.capitalize()} and {trend_phrase}"

    if signal == "BUY":
        return f"{base}; ranked using multi-horizon momentum with a volatility penalty."
    if signal == "AVOID":
        return f"{base}; does not meet the minimum momentum/trend buy conditions."
    return f"{base}; mixed signals, held at WAIT."


def generate_signal(momentum_60d, momentum_120d, above_ma50):
    """Simple three-condition buy filter, otherwise WAIT or AVOID."""
    if momentum_60d > 0 and momentum_120d > 0 and above_ma50:
        return "BUY"
    if momentum_60d <= 0 and momentum_120d <= 0 and not above_ma50:
        return "AVOID"
    return "WAIT"


def analyze(ticker, data):
    """Return the common strategy result for Momentum V2."""
    data = data.dropna(subset=["Close"])
    if len(data) < REQUIRED_HISTORY_DAYS:
        return None

    closing_prices = data["Close"]

    try:
        current_price = float(closing_prices.iloc[-1])
        momentum_20d = calculate_momentum(closing_prices, MOMENTUM_20D_DAYS)
        momentum_60d = calculate_momentum(closing_prices, MOMENTUM_60D_DAYS)
        momentum_120d = calculate_momentum(closing_prices, MOMENTUM_120D_DAYS)
        moving_average_50d = calculate_moving_average(
            closing_prices, MOVING_AVERAGE_50D_DAYS
        )
        volatility_20d = calculate_volatility(
            closing_prices, VOLATILITY_LOOKBACK_DAYS
        )
    except (KeyError, ValueError):
        return None

    if pd.isna(volatility_20d):
        return None

    above_ma50 = current_price > moving_average_50d
    trend_component = 1.0 if above_ma50 else 0.0

    raw_momentum_score = (
        WEIGHT_MOMENTUM_20D * momentum_20d
        + WEIGHT_MOMENTUM_60D * momentum_60d
        + WEIGHT_MOMENTUM_120D * momentum_120d
        + WEIGHT_TREND * trend_component
    )
    volatility_penalty = VOLATILITY_PENALTY_MULTIPLIER * volatility_20d
    final_score = raw_momentum_score - volatility_penalty

    signal = generate_signal(momentum_60d, momentum_120d, above_ma50)
    reason = _build_reason(signal, momentum_60d, momentum_120d, above_ma50)

    factor_details = {
        "Price": current_price,
        "Momentum 20D": momentum_20d,
        "Momentum 60D": momentum_60d,
        "Momentum 120D": momentum_120d,
        "Moving Average 50D": moving_average_50d,
        "Above MA50": above_ma50,
        "Volatility 20D": volatility_20d,
        "Raw Momentum Score": raw_momentum_score,
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
    "volatility_lookback_days": VOLATILITY_LOOKBACK_DAYS,
    "weight_momentum_20d": WEIGHT_MOMENTUM_20D,
    "weight_momentum_60d": WEIGHT_MOMENTUM_60D,
    "weight_momentum_120d": WEIGHT_MOMENTUM_120D,
    "weight_trend": WEIGHT_TREND,
    "volatility_penalty_multiplier": VOLATILITY_PENALTY_MULTIPLIER,
}
