"""First-pass mapping from market regime to recommended strategies.

This module is purely a lookup layer: it maps `market.regime` regime names
to which *existing* strategies are best suited (and which to avoid), plus a
human-readable reason. It never selects positions, places trades, or
modifies any strategy - it only produces a recommendation for display.

Strategy names below are plain strings, not imports of strategy modules, so
this mapping stays valid even for strategies that are recommended for a
regime but not yet implemented in `strategies/registry.py` (this is called
out explicitly via `notes` below and surfaced by `describe_availability`).
"""

from dataclasses import dataclass, field

from market.regime import (
    DOWNTREND,
    HIGH_VOLATILITY,
    SIDEWAYS,
    STRONG_UPTREND,
    WEAK_UPTREND,
)


@dataclass(frozen=True)
class StrategyRecommendation:
    """A regime's preferred/avoided strategies plus the reasoning behind it."""

    regime: str
    preferred_strategies: tuple = ()
    strategies_to_avoid: tuple = ()
    reason: str = ""
    notes: tuple = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# First-pass regime -> strategy mapping. Nothing here is tuned from
# backtest results - it is a readable starting point that a human can
# revise later.
# ---------------------------------------------------------------------------
_STRATEGY_MAP = {
    STRONG_UPTREND: StrategyRecommendation(
        regime=STRONG_UPTREND,
        preferred_strategies=(
            "Aggressive Momentum V1",
            "Breakout Volume V1",
            "Relative Strength Momentum V1",
        ),
        strategies_to_avoid=(),
        reason=(
            "Broad market trend is strongly bullish with confirming momentum in "
            "both SPY and QQQ, favoring momentum/breakout strategies that chase "
            "strength."
        ),
    ),
    WEAK_UPTREND: StrategyRecommendation(
        regime=WEAK_UPTREND,
        preferred_strategies=("Momentum V2", "Relative Strength Momentum V1"),
        strategies_to_avoid=(),
        reason=(
            "Market is generally above long-term trend but momentum is mixed or "
            "weaker, favoring steadier momentum strategies over aggressive ones."
        ),
    ),
    SIDEWAYS: StrategyRecommendation(
        regime=SIDEWAYS,
        preferred_strategies=("Baseline",),
        strategies_to_avoid=(),
        reason=(
            "Market lacks clear direction and momentum is low in absolute terms; "
            "Baseline is used as a conservative placeholder for this regime."
        ),
        notes=(
            "A dedicated Mean Reversion strategy is not yet implemented and is "
            "recommended future work for SIDEWAYS markets.",
        ),
    ),
    DOWNTREND: StrategyRecommendation(
        regime=DOWNTREND,
        preferred_strategies=(),
        strategies_to_avoid=(
            "Aggressive Momentum V1",
            "Breakout Volume V1",
            "Relative Strength Momentum V1",
            "Momentum V2",
            "Baseline",
        ),
        reason=(
            "Broad market trend is bearish. No existing strategy is designed to "
            "perform well in a downtrend, so cash / WATCH is preferred over "
            "forcing a long trade."
        ),
        notes=(
            "No bearish/short strategy exists yet; a future dedicated bearish "
            "strategy is recommended for DOWNTREND markets.",
        ),
    ),
    HIGH_VOLATILITY: StrategyRecommendation(
        regime=HIGH_VOLATILITY,
        preferred_strategies=("Momentum V2",),
        strategies_to_avoid=("Aggressive Momentum V1",),
        reason=(
            "Elevated volatility raises whipsaw risk; favor the steadier, "
            "cash-biased Momentum V2 approach and avoid strategies tuned to "
            "chase aggressive momentum."
        ),
        notes=(
            "Preferred strategy is intentionally cash-biased/steady rather than "
            "aggressive during HIGH_VOLATILITY.",
        ),
    ),
}


def recommend_strategy(regime_result):
    """Return the `StrategyRecommendation` for a `RegimeResult` or regime name."""
    regime_name = getattr(regime_result, "regime", regime_result)
    try:
        return _STRATEGY_MAP[regime_name]
    except KeyError as error:
        raise ValueError(f"No strategy mapping defined for regime '{regime_name}'") from error


def all_strategy_mappings():
    """Return the full regime -> recommendation mapping (for display/tests)."""
    return dict(_STRATEGY_MAP)


def describe_availability(strategy_names):
    """Pair each strategy name with whether it is currently registered.

    This lets callers (e.g. the dashboard) clearly flag recommended
    strategies that are not yet implemented, without this module importing
    strategy modules directly or the mapping above breaking when a
    recommended strategy does not exist yet.
    """
    from strategies.registry import available_strategy_names

    available = set(available_strategy_names())
    return [(name, name in available) for name in strategy_names]
