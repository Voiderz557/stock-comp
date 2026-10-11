"""Research-only breadth/volatility participation control.

Adaptive Participation V1 does not create stock signals.  It delegates stock
selection and ranking to the unchanged Controlled Breakout V1 strategy, then
chooses whether the research portfolio may hold zero, two, or five of those
candidates.  The constants below are preregistered starting values and must not
be optimized against the validation results produced by this repository.

This module intentionally lives under ``benchmarking`` rather than
``strategies``: it is an allocation experiment, not a live-approved strategy.
"""

from dataclasses import asdict, dataclass

from market.regime import DOWNTREND, HIGH_VOLATILITY, SIDEWAYS
from strategies.registry import get_strategy


ADAPTIVE_PARTICIPATION_METHOD_NAME = "Adaptive Participation V1"
BASE_STRATEGY_NAME = "Controlled Breakout V1"

MIN_BREADTH_COVERAGE = 0.80
OFF_BREADTH_THRESHOLD = 0.40
FULL_BREADTH_THRESHOLD = 0.60
OFF_POSITION_COUNT = 0
HALF_POSITION_COUNT = 2
FULL_POSITION_COUNT = 5


class InvalidParticipationDataError(RuntimeError):
    """Required point-in-time inputs are unavailable or insufficient."""


@dataclass(frozen=True)
class ParticipationDecision:
    state: str
    top_n: int
    reason: str
    valid: bool
    regime: str | None
    breadth_coverage: float
    pct_above_ma50: float | None
    pct_positive_momentum_20d: float | None
    spy_above_ma200: bool | None
    qqq_above_ma200: bool | None

    def to_dict(self):
        return asdict(self)


def decide_participation(regime_result, breadth):
    """Return the preregistered OFF/HALF/FULL decision.

    Missing breadth or regime inputs fail closed as INVALID.  They are never
    converted to an OFF return, because a data failure must not masquerade as
    successful market timing.
    """
    regime = getattr(regime_result, "regime", None)
    features = getattr(regime_result, "features", None)
    coverage = float(getattr(breadth, "coverage_ratio", 0.0) or 0.0)
    above_ma50 = getattr(breadth, "pct_above_ma50", None)
    positive_momentum = getattr(breadth, "pct_positive_momentum_20d", None)
    spy_above_ma200 = (
        None if features is None else bool(features.spy_price > features.spy_ma200)
    )
    qqq_above_ma200 = (
        None if features is None else bool(features.qqq_price > features.qqq_ma200)
    )

    common = {
        "regime": regime,
        "breadth_coverage": coverage,
        "pct_above_ma50": above_ma50,
        "pct_positive_momentum_20d": positive_momentum,
        "spy_above_ma200": spy_above_ma200,
        "qqq_above_ma200": qqq_above_ma200,
    }
    if (
        regime is None
        or features is None
        or above_ma50 is None
        or positive_momentum is None
        or coverage < MIN_BREADTH_COVERAGE
    ):
        return ParticipationDecision(
            state="INVALID",
            top_n=OFF_POSITION_COUNT,
            reason=(
                "Required point-in-time regime/breadth data is incomplete: "
                f"breadth coverage {coverage:.1%}, minimum {MIN_BREADTH_COVERAGE:.1%}."
            ),
            valid=False,
            **common,
        )

    if regime == DOWNTREND or (
        above_ma50 < OFF_BREADTH_THRESHOLD
        and positive_momentum < OFF_BREADTH_THRESHOLD
    ):
        reason = (
            "DOWNTREND regime."
            if regime == DOWNTREND
            else "Both breadth measures are below the 40% OFF threshold."
        )
        return ParticipationDecision(
            state="OFF",
            top_n=OFF_POSITION_COUNT,
            reason=reason,
            valid=True,
            **common,
        )

    if (
        regime in {HIGH_VOLATILITY, SIDEWAYS}
        or not spy_above_ma200
        or not qqq_above_ma200
        or min(above_ma50, positive_momentum) < FULL_BREADTH_THRESHOLD
    ):
        reasons = []
        if regime in {HIGH_VOLATILITY, SIDEWAYS}:
            reasons.append(f"{regime} regime")
        if not spy_above_ma200 or not qqq_above_ma200:
            reasons.append("SPY and QQQ are not both above MA200")
        if min(above_ma50, positive_momentum) < FULL_BREADTH_THRESHOLD:
            reasons.append("at least one breadth measure is below 60%")
        return ParticipationDecision(
            state="HALF",
            top_n=HALF_POSITION_COUNT,
            reason="; ".join(reasons) + ".",
            valid=True,
            **common,
        )

    return ParticipationDecision(
        state="FULL",
        top_n=FULL_POSITION_COUNT,
        reason=(
            "Market is not DOWNTREND/HIGH_VOLATILITY/SIDEWAYS, both indices are "
            "above MA200, and both breadth measures are at least 60%."
        ),
        valid=True,
        **common,
    )


class AdaptiveParticipationStrategy:
    """Strategy-shaped research adapter around Controlled Breakout V1."""

    name = ADAPTIVE_PARTICIPATION_METHOD_NAME

    def __init__(self):
        self.base_strategy = get_strategy(BASE_STRATEGY_NAME)
        self.required_history_days = self.base_strategy.required_history_days
        self.decision_log = []

    def analyze(self, ticker, historical_data):
        return self.base_strategy.analyze(ticker, historical_data)

    def rank_key(self, result):
        return self.base_strategy.rank_key(result)

    def select_candidates(self, candidates, *, regime_result, breadth, rebalance_date):
        decision = decide_participation(regime_result, breadth)
        row = {"Date": rebalance_date, **decision.to_dict()}
        self.decision_log.append(row)
        if not decision.valid:
            raise InvalidParticipationDataError(decision.reason)
        return list(candidates[: decision.top_n]), decision


PARAMETERS = {
    "base_strategy": BASE_STRATEGY_NAME,
    "minimum_breadth_coverage": MIN_BREADTH_COVERAGE,
    "off_breadth_threshold": OFF_BREADTH_THRESHOLD,
    "full_breadth_threshold": FULL_BREADTH_THRESHOLD,
    "off_position_count": OFF_POSITION_COUNT,
    "half_position_count": HALF_POSITION_COUNT,
    "full_position_count": FULL_POSITION_COUNT,
}
