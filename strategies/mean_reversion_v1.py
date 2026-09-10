"""Mean Reversion V1: a long-only pullback strategy for SIDEWAYS / weak-trend
markets.

This strategy looks for stocks that have pulled back below their short-term
average (MA20) while still respecting their long-term trend (MA200), and
show simple signs of short-term stabilization rather than an accelerating
decline. It intentionally avoids anything that looks like a genuine
breakdown: price below MA200, very negative 20-day momentum, or extreme
volatility are all treated as reasons to AVOID rather than buy a "cheap"
stock.

Like every other strategy in this project, this is deliberately simple and
interpretable: no RSI smoothing beyond a plain moving average of gains and
losses, no machine learning, no news/event data, and no parameter
optimization against backtest results. All tunable constants live at the
top of this file.
"""

import pandas as pd


# ---------------------------------------------------------------------------
# Lookback windows (in trading days)
# ---------------------------------------------------------------------------
MOMENTUM_5D_DAYS = 5
MOMENTUM_20D_DAYS = 20
MOVING_AVERAGE_20D_DAYS = 20
MOVING_AVERAGE_50D_DAYS = 50
MOVING_AVERAGE_200D_DAYS = 200
RSI_PERIOD_DAYS = 14
VOLATILITY_LOOKBACK_DAYS = 20

# ---------------------------------------------------------------------------
# Required historical rows.
#
# MA200 is by far the longest lookback any factor here needs (200 rows).
# Every other factor (RSI14, MA50, MA20, both momentum windows, and the
# volatility window) needs comfortably fewer rows, so MA200 sets the floor.
# ---------------------------------------------------------------------------
REQUIRED_HISTORY_DAYS = max(
    MOVING_AVERAGE_200D_DAYS,
    MOVING_AVERAGE_50D_DAYS,
    MOVING_AVERAGE_20D_DAYS,
    RSI_PERIOD_DAYS + 1,
    MOMENTUM_20D_DAYS + 1,
    MOMENTUM_5D_DAYS + 1,
    VOLATILITY_LOOKBACK_DAYS + 1,
)

# ---------------------------------------------------------------------------
# Scoring weights for the four bounded [0, 1] sub-scores below. These sum to
# 1.0 before the volatility penalty is subtracted. Nothing here has been
# tuned against backtest results - it is a readable starting point.
# ---------------------------------------------------------------------------
WEIGHT_MA20_PULLBACK = 0.25
WEIGHT_RSI_OVERSOLD = 0.25
WEIGHT_MA50_PULLBACK = 0.15
WEIGHT_STABILIZATION = 0.15
WEIGHT_TREND_FILTER = 0.20

# ---------------------------------------------------------------------------
# MA20 / MA50 pullback scoring.
#
# `distance_from_maN = (price / maN) - 1` is negative when price sits below
# the average. We turn that into a [0, 1] "how meaningful is this pullback"
# score that reaches full credit once the pullback is at least
# `*_PULLBACK_FULL_CREDIT_DISTANCE` below the average, and gives zero credit
# at/above the average. The score is capped at 1.0 rather than continuing to
# grow for deeper drops, so an outright collapse is not scored as an
# increasingly attractive "pullback" - that is instead handled by the AVOID
# rules below.
# ---------------------------------------------------------------------------
MA20_PULLBACK_FULL_CREDIT_DISTANCE = 0.05
MA50_PULLBACK_FULL_CREDIT_DISTANCE = 0.08

# ---------------------------------------------------------------------------
# RSI oversold scoring.
#
# RSI at/above `RSI_OVERSOLD_HIGH` earns zero oversold credit (not oversold
# at all). RSI at/below `RSI_OVERSOLD_EXTREME_LOW` earns full credit (1.0).
# In between, credit increases linearly as RSI falls - i.e. a lower RSI
# always scores at least as well as a higher one, so long as both are below
# `RSI_OVERSOLD_HIGH`.
# ---------------------------------------------------------------------------
RSI_OVERSOLD_HIGH = 50.0
RSI_OVERSOLD_EXTREME_LOW = 10.0

# BUY requires RSI within this narrower "healthy pullback" band, not simply
# "oversold" - very low RSI outside this band is a sign of a possible
# capitulation/crash rather than a routine pullback, so it is left to WAIT.
RSI_BUY_LOW = 25.0
RSI_BUY_HIGH = 45.0

# ---------------------------------------------------------------------------
# Short-term stabilization scoring (5-day momentum).
#
# We want momentum that has cooled off - flat to modestly negative - rather
# than still accelerating downward (a falling knife) or already ripping
# higher (no longer a pullback entry). Credit is highest exactly at
# `STABILIZATION_IDEAL_MOMENTUM_5D` and decays linearly to zero once 5-day
# momentum is `STABILIZATION_WIDTH` away from that value in either
# direction.
# ---------------------------------------------------------------------------
STABILIZATION_IDEAL_MOMENTUM_5D = -0.01
STABILIZATION_WIDTH = 0.05

# BUY requires 5-day momentum to be "negative or near zero" - a small
# positive tolerance is allowed for "near zero".
MOMENTUM_5D_BUY_CEILING = 0.01

# ---------------------------------------------------------------------------
# 20-day momentum guardrails.
#
# BUY requires 20-day momentum to not be "severely negative" (above the
# floor below). AVOID triggers on "very negative" 20-day momentum, a more
# extreme threshold than the BUY floor - momentum between the two leaves the
# stock at WAIT (mixed setup).
# ---------------------------------------------------------------------------
MOMENTUM_20D_BUY_FLOOR = -0.08
MOMENTUM_20D_AVOID_THRESHOLD = -0.20

# ---------------------------------------------------------------------------
# Volatility.
#
# `volatility_20d` is the standard deviation of daily percentage returns
# over the last VOLATILITY_LOOKBACK_DAYS trading days, exactly as in
# Momentum V2. The penalty subtracted from the raw score is
# VOLATILITY_PENALTY_MULTIPLIER * volatility_20d. This strategy buys into
# weakness, so it uses a slightly larger multiplier than Momentum V2's 0.5
# to stay cautious about stocks that are merely "cheap" because they are
# unstable. AVOID triggers separately once volatility crosses the extreme
# threshold below - that is a "this may be collapsing" signal, not just a
# score penalty.
# ---------------------------------------------------------------------------
VOLATILITY_PENALTY_MULTIPLIER = 0.6
VOLATILITY_AVOID_THRESHOLD = 0.05


def calculate_moving_average(closing_prices, days):
    """Return the simple moving average of the last `days` closes."""
    if len(closing_prices) < days:
        raise ValueError("Not enough historical data.")
    return float(closing_prices.tail(days).mean())


def calculate_momentum(closing_prices, days):
    """Return (current_close / close_N_days_ago) - 1."""
    if len(closing_prices) < days + 1:
        raise ValueError("Not enough historical data.")
    current_price = closing_prices.iloc[-1]
    old_price = closing_prices.iloc[-(days + 1)]
    return float((current_price / old_price) - 1)


def calculate_volatility(closing_prices, days):
    """Return the standard deviation of daily % returns over the last `days`."""
    if len(closing_prices) < days + 1:
        raise ValueError("Not enough historical data.")
    daily_returns = closing_prices.pct_change().dropna()
    return float(daily_returns.tail(days).std())


def calculate_rsi(closing_prices, period=RSI_PERIOD_DAYS):
    """Standard 14-period RSI using a simple moving average of gains/losses.

    This intentionally uses a plain average over the lookback window rather
    than Wilder's exponential smoothing, to keep the calculation transparent
    and easy to verify by hand. RSI is bounded to [0, 100]; a flat price
    series (zero average loss) returns 100 (nothing to sell off from).
    """
    if len(closing_prices) < period + 1:
        raise ValueError("Not enough historical data.")
    deltas = closing_prices.diff().dropna().tail(period)
    gains = deltas.clip(lower=0)
    losses = -deltas.clip(upper=0)
    average_gain = gains.mean()
    average_loss = losses.mean()
    if average_loss == 0:
        return 100.0
    relative_strength = average_gain / average_loss
    return float(100 - (100 / (1 + relative_strength)))


def _clamp(value, lower=0.0, upper=1.0):
    return max(lower, min(upper, value))


def calculate_pullback_score(distance_from_average, full_credit_distance):
    """[0, 1] credit for being below a moving average, capped at full credit."""
    pullback_depth = max(0.0, -distance_from_average)
    return _clamp(pullback_depth / full_credit_distance)


def calculate_rsi_oversold_score(rsi14):
    """[0, 1] credit that increases as RSI falls, per the module docstring."""
    return _clamp(
        (RSI_OVERSOLD_HIGH - rsi14) / (RSI_OVERSOLD_HIGH - RSI_OVERSOLD_EXTREME_LOW)
    )


def calculate_stabilization_score(momentum_5d):
    """[0, 1] credit peaking at STABILIZATION_IDEAL_MOMENTUM_5D, per docstring."""
    distance = abs(momentum_5d - STABILIZATION_IDEAL_MOMENTUM_5D)
    return _clamp(1.0 - distance / STABILIZATION_WIDTH)


def _is_avoid(above_ma200, momentum_20d, volatility_20d):
    """AVOID for clearly bearish/collapsing conditions, per the module spec.

    A "clear breakdown" is, by construction, captured by these same three
    checks together (long-term trend broken, momentum severely negative,
    and/or volatility extreme) - there is no separate fourth trigger, since
    trading below MA50 alone is a normal, expected part of a healthy
    pullback and should not by itself disqualify a BUY.
    """
    if not above_ma200:
        return True
    if momentum_20d <= MOMENTUM_20D_AVOID_THRESHOLD:
        return True
    if volatility_20d >= VOLATILITY_AVOID_THRESHOLD:
        return True
    return False


def _is_buy(above_ma200, below_ma20, rsi14, momentum_5d, momentum_20d):
    """BUY: healthy long-term trend + meaningful pullback + oversold conditions."""
    rsi_in_buy_range = RSI_BUY_LOW <= rsi14 <= RSI_BUY_HIGH
    momentum_5d_cooling = momentum_5d <= MOMENTUM_5D_BUY_CEILING
    momentum_20d_not_severe = momentum_20d > MOMENTUM_20D_BUY_FLOOR
    return (
        above_ma200
        and below_ma20
        and rsi_in_buy_range
        and momentum_5d_cooling
        and momentum_20d_not_severe
    )


def generate_signal(above_ma200, below_ma20, rsi14, momentum_5d, momentum_20d, volatility_20d):
    """AVOID takes priority over BUY; anything else is WAIT (mixed setup)."""
    if _is_avoid(above_ma200, momentum_20d, volatility_20d):
        return "AVOID"
    if _is_buy(above_ma200, below_ma20, rsi14, momentum_5d, momentum_20d):
        return "BUY"
    return "WAIT"


def _build_reason(signal, above_ma200, below_ma20, rsi14, momentum_20d):
    trend_phrase = "above MA200" if above_ma200 else "below MA200"
    position_phrase = "below MA20" if below_ma20 else "at/above MA20"
    base = (
        f"Price is {trend_phrase} and {position_phrase}, RSI14={rsi14:.1f}, "
        f"20D momentum={momentum_20d:+.2%}"
    )
    if signal == "BUY":
        return f"{base}; healthy pullback with oversold conditions."
    if signal == "AVOID":
        return f"{base}; clearly bearish/breakdown conditions."
    return f"{base}; mixed setup, held at WAIT."


def analyze(ticker, data):
    """Return the common strategy result for Mean Reversion V1."""
    data = data.dropna(subset=["Close"])
    if len(data) < REQUIRED_HISTORY_DAYS:
        return None

    closing_prices = data["Close"]

    try:
        current_price = float(closing_prices.iloc[-1])
        ma20 = calculate_moving_average(closing_prices, MOVING_AVERAGE_20D_DAYS)
        ma50 = calculate_moving_average(closing_prices, MOVING_AVERAGE_50D_DAYS)
        ma200 = calculate_moving_average(closing_prices, MOVING_AVERAGE_200D_DAYS)
        rsi14 = calculate_rsi(closing_prices, RSI_PERIOD_DAYS)
        momentum_5d = calculate_momentum(closing_prices, MOMENTUM_5D_DAYS)
        momentum_20d = calculate_momentum(closing_prices, MOMENTUM_20D_DAYS)
        volatility_20d = calculate_volatility(closing_prices, VOLATILITY_LOOKBACK_DAYS)
    except (KeyError, ValueError):
        return None

    if pd.isna(volatility_20d) or pd.isna(rsi14):
        return None

    distance_from_ma20 = (current_price / ma20) - 1
    distance_from_ma50 = (current_price / ma50) - 1
    above_ma200 = current_price > ma200
    below_ma20 = current_price < ma20

    ma20_pullback_score = calculate_pullback_score(
        distance_from_ma20, MA20_PULLBACK_FULL_CREDIT_DISTANCE
    )
    ma50_pullback_score = calculate_pullback_score(
        distance_from_ma50, MA50_PULLBACK_FULL_CREDIT_DISTANCE
    )
    rsi_oversold_score = calculate_rsi_oversold_score(rsi14)
    stabilization_score = calculate_stabilization_score(momentum_5d)
    trend_filter_score = 1.0 if above_ma200 else 0.0

    raw_score = (
        WEIGHT_MA20_PULLBACK * ma20_pullback_score
        + WEIGHT_RSI_OVERSOLD * rsi_oversold_score
        + WEIGHT_MA50_PULLBACK * ma50_pullback_score
        + WEIGHT_STABILIZATION * stabilization_score
        + WEIGHT_TREND_FILTER * trend_filter_score
    )
    volatility_penalty = VOLATILITY_PENALTY_MULTIPLIER * volatility_20d
    final_score = raw_score - volatility_penalty

    signal = generate_signal(
        above_ma200, below_ma20, rsi14, momentum_5d, momentum_20d, volatility_20d
    )
    reason = _build_reason(signal, above_ma200, below_ma20, rsi14, momentum_20d)

    factor_details = {
        "Price": current_price,
        "MA20": ma20,
        "MA50": ma50,
        "MA200": ma200,
        "Distance from MA20": distance_from_ma20,
        "Distance from MA50": distance_from_ma50,
        "RSI14": rsi14,
        "Momentum 5D": momentum_5d,
        "Momentum 20D": momentum_20d,
        "Volatility 20D": volatility_20d,
        "Above MA200": above_ma200,
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
    """Higher final score ranks first; RSI14 (more oversold) breaks ties."""
    return (result["Score"], -result["RSI14"])


PARAMETERS = {
    "momentum_5d_days": MOMENTUM_5D_DAYS,
    "momentum_20d_days": MOMENTUM_20D_DAYS,
    "moving_average_20d_days": MOVING_AVERAGE_20D_DAYS,
    "moving_average_50d_days": MOVING_AVERAGE_50D_DAYS,
    "moving_average_200d_days": MOVING_AVERAGE_200D_DAYS,
    "rsi_period_days": RSI_PERIOD_DAYS,
    "volatility_lookback_days": VOLATILITY_LOOKBACK_DAYS,
    "weight_ma20_pullback": WEIGHT_MA20_PULLBACK,
    "weight_rsi_oversold": WEIGHT_RSI_OVERSOLD,
    "weight_ma50_pullback": WEIGHT_MA50_PULLBACK,
    "weight_stabilization": WEIGHT_STABILIZATION,
    "weight_trend_filter": WEIGHT_TREND_FILTER,
    "volatility_penalty_multiplier": VOLATILITY_PENALTY_MULTIPLIER,
    "rsi_buy_low": RSI_BUY_LOW,
    "rsi_buy_high": RSI_BUY_HIGH,
    "momentum_20d_buy_floor": MOMENTUM_20D_BUY_FLOOR,
    "momentum_20d_avoid_threshold": MOMENTUM_20D_AVOID_THRESHOLD,
    "volatility_avoid_threshold": VOLATILITY_AVOID_THRESHOLD,
}
