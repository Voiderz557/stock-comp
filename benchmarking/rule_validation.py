"""Chronological, coverage-gated validation for rule-based research candidates.

This intentionally has no ML, random period generation, parameter fitting, or
live-trading integration.  Every strategy receives the same ordered,
non-overlapping folds and the run fails closed when required point-in-time
constituent data is incomplete.
"""

from dataclasses import dataclass, field

import pandas as pd

from benchmarking.coverage import audit_price_coverage
from benchmarking.metrics import build_aggregate_table, build_period_results_table, build_period_row
from benchmarking.participation import (
    ADAPTIVE_PARTICIPATION_METHOD_NAME,
    AdaptiveParticipationStrategy,
)
from benchmarking.simulation import run_portfolio_simulation, unsupported_terminal_holdings
from strategies.registry import get_strategy


@dataclass
class RuleWalkForwardResult:
    folds: list
    period_table: pd.DataFrame
    aggregate_table: pd.DataFrame
    coverage: dict
    attribution: dict = field(default_factory=dict)


def validate_chronological_folds(folds):
    normalized = [(pd.Timestamp(start), pd.Timestamp(end)) for start, end in folds]
    if not normalized:
        raise ValueError("At least one validation fold is required.")
    for index, (start, end) in enumerate(normalized):
        if start >= end:
            raise ValueError("Every validation fold must have start < end.")
        if index and start <= normalized[index - 1][1]:
            raise ValueError("Validation folds must be chronological and non-overlapping.")
    return normalized


def run_rule_walk_forward(
    price_data,
    folds,
    strategy_names,
    *,
    universe=None,
    benchmark="SPY",
    progress_callback=None,
):
    """Run registered rule strategies on identical chronological folds."""
    folds = validate_chronological_folds(folds)
    coverage = audit_price_coverage(
        price_data, folds[0][0], folds[-1][1], universe=universe, benchmark=benchmark
    )
    if not coverage.get("Coverage Is Valid", False):
        missing = ", ".join(coverage.get("Unavailable Valid Constituents", [])) or "unknown"
        raise RuntimeError(
            "Clean walk-forward validation refused because required historical "
            f"coverage is incomplete: {missing}."
        )

    period_rows = []
    attribution = {
        "Regime Path": [],
        "Candidate Attribution": [],
        "Position Attribution": [],
        "Exposure History": [],
    }
    for fold_number, (start, end) in enumerate(folds, 1):
        for strategy_name in strategy_names:
            if progress_callback:
                progress_callback(fold_number, len(folds), strategy_name, start, end)
            strategy = (
                AdaptiveParticipationStrategy()
                if strategy_name == ADAPTIVE_PARTICIPATION_METHOD_NAME
                else get_strategy(strategy_name)
            )
            result = run_portfolio_simulation(
                strategy,
                start,
                end,
                price_data,
                benchmark=benchmark,
                method_name=strategy_name,
                universe=universe,
            )
            terminal = unsupported_terminal_holdings(result)
            if terminal:
                raise RuntimeError(
                    "Clean walk-forward validation refused because a terminal corporate "
                    f"action is not modeled: {terminal[0]['Ticker']}."
                )
            period_rows.append(build_period_row(result, fold_number))
            common = {"Fold": fold_number, "Method": strategy_name}
            for key in attribution:
                attribution[key].extend({**common, **row} for row in result.get(key, []))

    period_table = build_period_results_table(period_rows)
    expected = {(fold, method) for fold in range(1, len(folds) + 1) for method in strategy_names}
    actual = set(zip(period_table["Test"], period_table["Method"]))
    if actual != expected:
        raise RuntimeError("Not every strategy completed every identical validation fold.")
    return RuleWalkForwardResult(
        folds=folds,
        period_table=period_table,
        aggregate_table=build_aggregate_table(period_table),
        coverage=coverage,
        attribution={key: pd.DataFrame(rows) for key, rows in attribution.items()},
    )
