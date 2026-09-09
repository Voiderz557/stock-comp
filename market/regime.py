"""Rule-based market regime classification using broad-market data.

This module classifies the current market into one of five simple,
human-readable regimes using SPY and QQQ price history only:

    STRONG_UPTREND, WEAK_UPTREND, SIDEWAYS, DOWNTREND, HIGH_VOLATILITY

There is no machine learning, news, or event data here - every feature is a
simple moving average, momentum, or volatility calculation, and every
threshold is a named constant defined below. This keeps the classification
fully transparent and easy to adjust.

`compute_market_features` and `classify_market_regime` are pure functions:
they take price DataFrames / a `MarketFeatures` instance and return a
result, with no data loading or I/O. `load_current_market_regime` is the
only function in this module that touches the data layer, and it exists
purely as a convenience for callers (e.g. the dashboard) that want "today's"
regime without wiring up the data loading themselves.
"""

from dataclasses import dataclass, field

import pandas as pd


# ---------------------------------------------------------------------------
# Regime names
# ---------------------------------------------------------------------------
STRONG_UPTREND = "STRONG_UPTREND"
WEAK_UPTREND = "WEAK_UPTREND"
SIDEWAYS = "SIDEWAYS"
DOWNTREND = "DOWNTREND"
HIGH_VOLATILITY = "HIGH_VOLATILITY"

ALL_REGIMES = (
    STRONG_UPTREND,
    WEAK_UPTREND,
    SIDEWAYS,
    DOWNTREND,
    HIGH_VOLATILITY,
)

# ---------------------------------------------------------------------------
# Lookback windows (in trading days)
# ---------------------------------------------------------------------------
MOMENTUM_SHORT_DAYS = 20
MOMENTUM_LONG_DAYS = 60
MOVING_AVERAGE_SHORT_DAYS = 50
MOVING_AVERAGE_LONG_DAYS = 200
VOLATILITY_LOOKBACK_DAYS = 20

# The moving-average-200 window is the longest lookback any feature needs.
REQUIRED_HISTORY_DAYS = max(
    MOVING_AVERAGE_LONG_DAYS,
    MOMENTUM_LONG_DAYS + 1,
    MOMENTUM_SHORT_DAYS + 1,
    VOLATILITY_LOOKBACK_DAYS + 1,
)

# When loading "current" market data for live use, request extra calendar
# days beyond REQUIRED_HISTORY_DAYS trading days to comfortably absorb
# weekends/holidays.
LIVE_LOOKBACK_CALENDAR_DAYS = 400

# ---------------------------------------------------------------------------
# Regime thresholds. All named and readable - nothing here is fit to
# historical results.
# ---------------------------------------------------------------------------

# 20-day standard deviation of daily returns. At/above this level, volatility
# is considered extreme enough to override any directional regime.
HIGH_VOLATILITY_THRESHOLD = 0.020

# Used only to build a human-readable confidence score for HIGH_VOLATILITY:
# volatility at HIGH_VOLATILITY_THRESHOLD * this scale maps to 100% confidence.
HIGH_VOLATILITY_CONFIDENCE_SCALE = 2.0

# A 20D or 60D momentum reading with absolute value below this threshold is
# considered "low absolute momentum" (part of the SIDEWAYS definition).
SIDEWAYS_MOMENTUM_THRESHOLD = 0.02


@dataclass(frozen=True)
class MarketFeatures:
    """Simple, interpretable broad-market features derived from SPY/QQQ."""

    spy_price: float
    spy_ma50: float
    spy_ma200: float
    spy_momentum_20d: float
    spy_momentum_60d: float
    spy_volatility_20d: float

    qqq_price: float
    qqq_ma50: float
    qqq_ma200: float
    qqq_momentum_20d: float
    qqq_momentum_60d: float
    qqq_volatility_20d: float

    market_volatility_20d: float


@dataclass(frozen=True)
class RegimeResult:
    """The classified regime plus everything needed to explain the call."""

    regime: str
    confidence: float
    reason: str
    features: MarketFeatures
    supporting_metrics: dict = field(default_factory=dict)


def _moving_average(closes, window):
    return float(closes.iloc[-window:].mean())


def _momentum(closes, days):
    """(current_close / close_N_days_ago) - 1."""
    current_price = closes.iloc[-1]
    old_price = closes.iloc[-(days + 1)]
    return float((current_price / old_price) - 1)


def _daily_return_volatility(closes, window):
    """Standard deviation of daily % returns over the last `window` days."""
    daily_returns = closes.pct_change().dropna()
    return float(daily_returns.iloc[-window:].std())


def compute_market_features(spy_data, qqq_data):
    """Build a `MarketFeatures` snapshot from SPY/QQQ price DataFrames.

    Both `spy_data` and `qqq_data` must contain a "Close" column with at
    least `REQUIRED_HISTORY_DAYS` rows, ordered oldest-to-newest, with the
    most recent row representing "today". This function reads only the
    rows it is given (no external lookups), so it is safe to test with
    synthetic data.
    """
    spy_closes = spy_data["Close"].dropna()
    qqq_closes = qqq_data["Close"].dropna()

    if len(spy_closes) < REQUIRED_HISTORY_DAYS:
        raise ValueError(
            f"SPY history too short: need at least {REQUIRED_HISTORY_DAYS} "
            f"rows, got {len(spy_closes)}."
        )
    if len(qqq_closes) < REQUIRED_HISTORY_DAYS:
        raise ValueError(
            f"QQQ history too short: need at least {REQUIRED_HISTORY_DAYS} "
            f"rows, got {len(qqq_closes)}."
        )

    spy_volatility_20d = _daily_return_volatility(spy_closes, VOLATILITY_LOOKBACK_DAYS)
    qqq_volatility_20d = _daily_return_volatility(qqq_closes, VOLATILITY_LOOKBACK_DAYS)

    return MarketFeatures(
        spy_price=float(spy_closes.iloc[-1]),
        spy_ma50=_moving_average(spy_closes, MOVING_AVERAGE_SHORT_DAYS),
        spy_ma200=_moving_average(spy_closes, MOVING_AVERAGE_LONG_DAYS),
        spy_momentum_20d=_momentum(spy_closes, MOMENTUM_SHORT_DAYS),
        spy_momentum_60d=_momentum(spy_closes, MOMENTUM_LONG_DAYS),
        spy_volatility_20d=spy_volatility_20d,
        qqq_price=float(qqq_closes.iloc[-1]),
        qqq_ma50=_moving_average(qqq_closes, MOVING_AVERAGE_SHORT_DAYS),
        qqq_ma200=_moving_average(qqq_closes, MOVING_AVERAGE_LONG_DAYS),
        qqq_momentum_20d=_momentum(qqq_closes, MOMENTUM_SHORT_DAYS),
        qqq_momentum_60d=_momentum(qqq_closes, MOMENTUM_LONG_DAYS),
        qqq_volatility_20d=qqq_volatility_20d,
        market_volatility_20d=(spy_volatility_20d + qqq_volatility_20d) / 2,
    )


def _fraction_true(values):
    values = list(values)
    if not values:
        return 0.0
    return sum(1 for value in values if value) / len(values)


def classify_market_regime(features):
    """Classify `features` into one of `ALL_REGIMES` using transparent rules.

    Evaluation order (first match wins):

    1. HIGH_VOLATILITY - overrides every directional regime.
    2. STRONG_UPTREND - both SPY and QQQ in a clean uptrend
       (price > MA50 > MA200) with positive 20D and 60D momentum on both.
    3. DOWNTREND - SPY and/or QQQ below its short/long trend AND momentum
       broadly negative on at least one index.
    4. SIDEWAYS - all four momentum readings have low absolute value.
    5. WEAK_UPTREND - generally above long-term trend (price > MA200 on
       both) but did not qualify as STRONG_UPTREND or SIDEWAYS.
    6. SIDEWAYS - fallback for anything else (mixed signals, not clearly
       bullish or bearish).
    """
    spy_strong_trend = features.spy_price > features.spy_ma50 > features.spy_ma200
    qqq_strong_trend = features.qqq_price > features.qqq_ma50 > features.qqq_ma200

    spy_momentum_positive = (
        features.spy_momentum_20d > 0 and features.spy_momentum_60d > 0
    )
    qqq_momentum_positive = (
        features.qqq_momentum_20d > 0 and features.qqq_momentum_60d > 0
    )

    spy_below_trend = (
        features.spy_price < features.spy_ma50
        or features.spy_price < features.spy_ma200
    )
    qqq_below_trend = (
        features.qqq_price < features.qqq_ma50
        or features.qqq_price < features.qqq_ma200
    )
    spy_momentum_negative = (
        features.spy_momentum_20d < 0 and features.spy_momentum_60d < 0
    )
    qqq_momentum_negative = (
        features.qqq_momentum_20d < 0 and features.qqq_momentum_60d < 0
    )

    low_absolute_momentum = all(
        abs(value) < SIDEWAYS_MOMENTUM_THRESHOLD
        for value in (
            features.spy_momentum_20d,
            features.spy_momentum_60d,
            features.qqq_momentum_20d,
            features.qqq_momentum_60d,
        )
    )
    above_long_term_trend = (
        features.spy_price > features.spy_ma200
        and features.qqq_price > features.qqq_ma200
    )
    high_volatility = features.market_volatility_20d >= HIGH_VOLATILITY_THRESHOLD

    supporting_metrics = {
        "SPY price > MA50 > MA200": spy_strong_trend,
        "QQQ price > MA50 > MA200": qqq_strong_trend,
        "SPY 20D & 60D momentum positive": spy_momentum_positive,
        "QQQ 20D & 60D momentum positive": qqq_momentum_positive,
        "SPY below MA50 or MA200": spy_below_trend,
        "QQQ below MA50 or MA200": qqq_below_trend,
        "SPY 20D & 60D momentum negative": spy_momentum_negative,
        "QQQ 20D & 60D momentum negative": qqq_momentum_negative,
        "All momentum readings low (|x| < threshold)": low_absolute_momentum,
        "Price above MA200 on both indices": above_long_term_trend,
        "Volatility >= high-volatility threshold": high_volatility,
    }

    if high_volatility:
        confidence = min(
            1.0,
            features.market_volatility_20d
            / (HIGH_VOLATILITY_THRESHOLD * HIGH_VOLATILITY_CONFIDENCE_SCALE),
        )
        reason = (
            f"20D market volatility ({features.market_volatility_20d:.2%}) is at or "
            f"above the high-volatility threshold ({HIGH_VOLATILITY_THRESHOLD:.2%}); "
            "this overrides any directional read on the market."
        )
        return RegimeResult(HIGH_VOLATILITY, confidence, reason, features, supporting_metrics)

    if spy_strong_trend and qqq_strong_trend and spy_momentum_positive and qqq_momentum_positive:
        confidence = _fraction_true(
            [
                spy_strong_trend,
                qqq_strong_trend,
                features.spy_momentum_20d > 0,
                features.spy_momentum_60d > 0,
                features.qqq_momentum_20d > 0,
                features.qqq_momentum_60d > 0,
            ]
        )
        reason = (
            "SPY and QQQ are both above MA50, which is above MA200, with positive "
            "20D and 60D momentum on both indices, and volatility is not extreme."
        )
        return RegimeResult(STRONG_UPTREND, confidence, reason, features, supporting_metrics)

    if (spy_below_trend or qqq_below_trend) and (spy_momentum_negative or qqq_momentum_negative):
        confidence = _fraction_true(
            [spy_below_trend, qqq_below_trend, spy_momentum_negative, qqq_momentum_negative]
        )
        reason = (
            "SPY and/or QQQ are trading below their MA50/MA200, alongside broadly "
            "negative 20D/60D momentum on at least one index."
        )
        return RegimeResult(DOWNTREND, confidence, reason, features, supporting_metrics)

    if low_absolute_momentum:
        confidence = _fraction_true(
            [
                abs(features.spy_momentum_20d) < SIDEWAYS_MOMENTUM_THRESHOLD,
                abs(features.spy_momentum_60d) < SIDEWAYS_MOMENTUM_THRESHOLD,
                abs(features.qqq_momentum_20d) < SIDEWAYS_MOMENTUM_THRESHOLD,
                abs(features.qqq_momentum_60d) < SIDEWAYS_MOMENTUM_THRESHOLD,
            ]
        )
        reason = (
            "20D and 60D momentum on both SPY and QQQ are all within "
            f"+/-{SIDEWAYS_MOMENTUM_THRESHOLD:.0%}, indicating low absolute momentum "
            "with no clear directional read."
        )
        return RegimeResult(SIDEWAYS, confidence, reason, features, supporting_metrics)

    if above_long_term_trend:
        confidence = _fraction_true(
            [
                features.spy_price > features.spy_ma200,
                features.qqq_price > features.qqq_ma200,
                features.spy_momentum_20d > 0 or features.spy_momentum_60d > 0,
                features.qqq_momentum_20d > 0 or features.qqq_momentum_60d > 0,
            ]
        )
        reason = (
            "SPY and QQQ are both above their MA200 (generally above long-term "
            "trend), but momentum/trend signals are mixed or weaker than the "
            "STRONG_UPTREND requirements."
        )
        return RegimeResult(WEAK_UPTREND, confidence, reason, features, supporting_metrics)

    confidence = 1.0 - _fraction_true(
        [spy_strong_trend, qqq_strong_trend, spy_below_trend, qqq_below_trend]
    )
    reason = (
        "Trend and momentum signals across SPY/QQQ are mixed and do not clearly "
        "satisfy any other regime; defaulting to SIDEWAYS."
    )
    return RegimeResult(SIDEWAYS, confidence, reason, features, supporting_metrics)


def load_current_market_regime(load_market_data_fn=None, as_of=None, cache_dir=None):
    """Convenience wrapper: load recent SPY/QQQ data and classify "today".

    This is the only function in this module that touches the data layer.
    It is intentionally a thin wrapper around `compute_market_features` and
    `classify_market_regime` so the classification logic itself stays fully
    unit-testable with synthetic data.
    """
    if load_market_data_fn is None:
        from data.market_data import load_market_data as load_market_data_fn

    as_of = pd.Timestamp(as_of).normalize() if as_of is not None else pd.Timestamp.today().normalize()
    start_date = as_of - pd.Timedelta(days=LIVE_LOOKBACK_CALENDAR_DAYS)

    data, cache_info = load_market_data_fn(
        ["SPY", "QQQ"], start_date, as_of, cache_dir=cache_dir
    )
    spy_data = data.get("SPY")
    qqq_data = data.get("QQQ")
    if spy_data is None or spy_data.empty or qqq_data is None or qqq_data.empty:
        raise ValueError("Could not load sufficient SPY/QQQ data to classify the market regime.")

    features = compute_market_features(spy_data, qqq_data)
    return classify_market_regime(features), cache_info
