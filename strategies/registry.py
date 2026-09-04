"""Readable strategy-name registry used by every application entry point."""

from dataclasses import dataclass
from typing import Callable

from . import baseline


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
    )
}


def available_strategy_names():
    return list(_STRATEGIES)


def get_strategy(name):
    try:
        return _STRATEGIES[name]
    except KeyError as error:
        choices = ", ".join(available_strategy_names())
        raise ValueError(f"Unknown strategy '{name}'. Available: {choices}") from error
