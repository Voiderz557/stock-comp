"""Readable strategy-name registry used by every application entry point."""

import inspect
from dataclasses import dataclass
from typing import Callable, Optional

from . import (
    aggressive_momentum_v1,
    baseline,
    breakout_volume_v1,
    momentum_v2,
    relative_strength_momentum_v1,
)


@dataclass(frozen=True)
class StrategyDefinition:
    name: str
    analyze: Callable
    rank_key: Callable
    parameters: dict
    required_history_days: int
    benchmark_ticker: Optional[str] = None


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
    "Aggressive Momentum V1": StrategyDefinition(
        name="Aggressive Momentum V1",
        analyze=aggressive_momentum_v1.analyze,
        rank_key=aggressive_momentum_v1.rank_key,
        parameters=aggressive_momentum_v1.PARAMETERS,
        required_history_days=aggressive_momentum_v1.REQUIRED_HISTORY_DAYS,
    ),
    "Relative Strength Momentum V1": StrategyDefinition(
        name="Relative Strength Momentum V1",
        analyze=relative_strength_momentum_v1.analyze,
        rank_key=relative_strength_momentum_v1.rank_key,
        parameters=relative_strength_momentum_v1.PARAMETERS,
        required_history_days=relative_strength_momentum_v1.REQUIRED_HISTORY_DAYS,
        benchmark_ticker=relative_strength_momentum_v1.DEFAULT_BENCHMARK_TICKER,
    ),
    "Breakout Volume V1": StrategyDefinition(
        name="Breakout Volume V1",
        analyze=breakout_volume_v1.analyze,
        rank_key=breakout_volume_v1.rank_key,
        parameters=breakout_volume_v1.PARAMETERS,
        required_history_days=breakout_volume_v1.REQUIRED_HISTORY_DAYS,
    ),
}


def invoke_analyze(analyze, ticker, data, benchmark_data=None):
    """Call a strategy analyze() function, passing benchmark history only
    when that function declares a `benchmark_data` argument.

    Existing strategies stay on the (ticker, data) contract. Relative
    Strength Momentum V1 receives the already-loaded benchmark frame from
    the data/backtest layer.
    """
    try:
        signature = inspect.signature(analyze)
    except (TypeError, ValueError):
        return analyze(ticker, data)

    accepts_benchmark = "benchmark_data" in signature.parameters or any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
    )
    if accepts_benchmark:
        return analyze(ticker, data, benchmark_data=benchmark_data)
    return analyze(ticker, data)


def available_strategy_names():
    return list(_STRATEGIES)


def get_strategy(name):
    try:
        return _STRATEGIES[name]
    except KeyError as error:
        choices = ", ".join(available_strategy_names())
        raise ValueError(f"Unknown strategy '{name}'. Available: {choices}") from error
