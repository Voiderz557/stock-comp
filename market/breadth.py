"""Point-in-time market breadth calculated from a historical universe snapshot."""

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd


MA_SHORT_DAYS = 50
MA_LONG_DAYS = 200
MOMENTUM_DAYS = 20
PRIOR_HIGH_DAYS = 20
NEAR_PRIOR_HIGH_THRESHOLD = -0.02
REQUIRED_HISTORY_DAYS = max(MA_LONG_DAYS, MOMENTUM_DAYS + 1, PRIOR_HIGH_DAYS + 1)


@dataclass(frozen=True)
class MarketBreadth:
    as_of_date: pd.Timestamp | None
    universe_count: int
    eligible_count: int
    coverage_ratio: float
    pct_above_ma50: float | None
    pct_above_ma200: float | None
    pct_positive_momentum_20d: float | None
    pct_near_prior_20d_high: float | None
    median_momentum_20d: float | None

    def to_dict(self):
        return asdict(self)


def compute_market_breadth(price_data, universe, evaluation_date):
    """Return breadth using closes strictly before ``evaluation_date``.

    Warmup rows may precede the universe snapshot date, but only constituents
    in the supplied point-in-time snapshot enter the denominator. Securities
    lacking a complete 200-session history are disclosed through coverage and
    excluded from the factor denominator instead of being treated as bearish.
    """
    evaluation_date = pd.Timestamp(evaluation_date)
    observations = []
    as_of_dates = []
    normalized_universe = tuple(dict.fromkeys(str(t).strip().upper() for t in universe))

    for ticker in normalized_universe:
        frame = price_data.get(ticker) if isinstance(price_data, dict) else None
        if frame is None or frame.empty or "Close" not in frame:
            continue
        closes = pd.to_numeric(
            frame.loc[frame.index < evaluation_date, "Close"], errors="coerce"
        ).dropna()
        closes = closes[closes > 0]
        if len(closes) < REQUIRED_HISTORY_DAYS:
            continue
        price = float(closes.iloc[-1])
        momentum = price / float(closes.iloc[-(MOMENTUM_DAYS + 1)]) - 1
        prior_high = float(closes.iloc[-(PRIOR_HIGH_DAYS + 1):-1].max())
        observations.append(
            {
                "above_ma50": price > float(closes.iloc[-MA_SHORT_DAYS:].mean()),
                "above_ma200": price > float(closes.iloc[-MA_LONG_DAYS:].mean()),
                "positive_momentum": momentum > 0,
                "near_high": price / prior_high - 1 >= NEAR_PRIOR_HIGH_THRESHOLD,
                "momentum": momentum,
            }
        )
        as_of_dates.append(pd.Timestamp(closes.index[-1]))

    count = len(observations)
    universe_count = len(normalized_universe)
    if not count:
        return MarketBreadth(
            as_of_date=None,
            universe_count=universe_count,
            eligible_count=0,
            coverage_ratio=0.0 if universe_count else 1.0,
            pct_above_ma50=None,
            pct_above_ma200=None,
            pct_positive_momentum_20d=None,
            pct_near_prior_20d_high=None,
            median_momentum_20d=None,
        )

    return MarketBreadth(
        as_of_date=max(as_of_dates),
        universe_count=universe_count,
        eligible_count=count,
        coverage_ratio=count / universe_count if universe_count else 1.0,
        pct_above_ma50=float(np.mean([row["above_ma50"] for row in observations])),
        pct_above_ma200=float(np.mean([row["above_ma200"] for row in observations])),
        pct_positive_momentum_20d=float(
            np.mean([row["positive_momentum"] for row in observations])
        ),
        pct_near_prior_20d_high=float(np.mean([row["near_high"] for row in observations])),
        median_momentum_20d=float(np.median([row["momentum"] for row in observations])),
    )
