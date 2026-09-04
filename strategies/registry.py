"""Readable strategy-name registry used by every application entry point."""

from dataclasses import dataclass
from typing import Callable

from . import baseline, momentum_v2


@dataclass(frozen=True)
class StrategyDefinition:
    name: str
    analyze: Callable
    rank_key: Callable
    parameters: dict
    required_history_days: int


_STRATEGIES = {
    "Baseline": StrategyDefinition(
        name="Baseline",
        analyze=baseline.analyze,
        rank_key=baseline.rank_key,
        parameters=baseline.PARAMETERS,
        required_history_days=baseline.REQUIRED_HISTORY_DAYS,
    ),
    "Momentum V2": StrategyDefinition(
        name="Momentum V2",
        analyze=momentum_v2.analyze,
        rank_key=momentum_v2.rank_key,
        parameters=momentum_v2.PARAMETERS,
        required_history_days=momentum_v2.REQUIRED_HISTORY_DAYS,
    ),
}


def available_strategy_names():
    return list(_STRATEGIES)


def get_strategy(name):
    try:
        return _STRATEGIES[name]
    except KeyError as error:
        choices = ", ".join(available_strategy_names())
        raise ValueError(f"Unknown strategy '{name}'. Available: {choices}") from error
