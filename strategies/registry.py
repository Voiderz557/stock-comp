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


_STRATEGIES = {
    "Baseline": StrategyDefinition(
        name="Baseline",
        analyze=baseline.analyze,
        rank_key=baseline.rank_key,
        parameters=baseline.PARAMETERS,
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
