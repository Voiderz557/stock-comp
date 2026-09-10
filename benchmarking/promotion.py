"""Transparent PROMOTE / RESEARCH_MORE / REJECT recommendation.

This is a research recommendation only - nothing here connects a promoted
model to paper trading or live trading. Every threshold is a named
constant below so the rule stays auditable and easy to revise.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import pandas as pd

PROMOTE = "PROMOTE"
RESEARCH_MORE = "RESEARCH_MORE"
REJECT = "REJECT"
INVALID = "INVALID"

# --- Named thresholds -------------------------------------------------------
# 1. Median excess return vs SPY must be positive.
PROMOTION_MIN_MEDIAN_EXCESS_RETURN = 0.0
# 2. Beat-SPY rate across all tested periods.
PROMOTION_MIN_BEAT_SPY_RATE = 0.55
# 3. Either match/beat the best existing strategy's P(Return >= 20%), or
#    clearly improve risk-adjusted return (Sharpe Ratio).
PROMOTION_COMPETITION_THRESHOLD_METRIC = "P(Return >= 20%)"
# 4. The ML method's single worst period must not be materially worse than
#    the best existing strategy's single worst period (absolute tolerance).
PROMOTION_WORST_PERIOD_TOLERANCE = 0.05
# 5. Performance must hold across a meaningful number of independent periods
#    ("folds") - too few periods cannot support a PROMOTE recommendation.
PROMOTION_MIN_PERIODS_TESTED = 10
# 6. No single period may contribute more than this share of the method's
#    total positive return across all periods (guards against one lucky
#    winner driving the whole result).
PROMOTION_MAX_SINGLE_PERIOD_CONTRIBUTION_SHARE = 0.5

# A REJECT (rather than RESEARCH_MORE) is issued outright when performance
# is unambiguously bad on both of the two most important axes at once.
PROMOTION_REJECT_MEDIAN_EXCESS_RETURN = 0.0
PROMOTION_REJECT_BEAT_SPY_RATE = 0.50


@dataclass(frozen=True)
class PromotionResult:
    """The output of `evaluate_promotion`: a decision plus the full rationale."""

    method: str
    compared_against: str
    decision: str
    criteria: dict = field(default_factory=dict)
    reasons: tuple = field(default_factory=tuple)


def _is_finite(value):
    return value is not None and not (isinstance(value, float) and math.isnan(value))


def evaluate_promotion(
    method_name,
    ml_metrics,
    best_existing_metrics,
    best_existing_method_name,
    ml_period_returns,
    leakage_audit_is_valid=True,
):
    """Apply the promotion gate to one ML method's aggregate metrics.

    `ml_metrics`/`best_existing_metrics` are `benchmarking.metrics.compute_aggregate_metrics`
    dicts. `ml_period_returns` is the list of per-period `Total Return` values
    for this ML method, used for the "no single fold dominates" check.
    """
    if not leakage_audit_is_valid:
        return PromotionResult(
            method=method_name,
            compared_against=best_existing_method_name,
            decision=INVALID,
            criteria={},
            reasons=(
                "Leakage audit FAILED: this benchmark is marked INVALID. "
                "No PROMOTE recommendation can be produced.",
            ),
        )

    if not ml_metrics:
        return PromotionResult(
            method=method_name,
            compared_against=best_existing_method_name,
            decision=RESEARCH_MORE,
            criteria={},
            reasons=("Required metrics unavailable: no usable results were produced for this method.",),
        )

    best_existing_metrics = best_existing_metrics or {}

    def _required(metrics, key):
        if key not in metrics:
            return None
        value = metrics[key]
        return value if _is_finite(value) else None

    median_excess_return = _required(ml_metrics, "Median Excess Return")
    beat_spy_rate = _required(ml_metrics, "Beat SPY %")
    periods_tested = ml_metrics.get("Periods Tested")
    if not _is_finite(periods_tested):
        periods_tested = 0

    ml_threshold_probability = _required(ml_metrics, PROMOTION_COMPETITION_THRESHOLD_METRIC)
    best_threshold_probability = _required(best_existing_metrics, PROMOTION_COMPETITION_THRESHOLD_METRIC)
    ml_sharpe = _required(ml_metrics, "Sharpe Ratio")
    best_sharpe = _required(best_existing_metrics, "Sharpe Ratio")
    risk_adjusted_clearly_improved = (
        ml_sharpe is not None and best_sharpe is not None and ml_sharpe > best_sharpe
    )

    worst_ml_period = _required(ml_metrics, "Worst Period Return")
    worst_existing_period = _required(best_existing_metrics, "Worst Period Return")

    positive_returns = [value for value in ml_period_returns if value > 0]
    total_positive_return = sum(positive_returns)
    max_single_period_return = max(positive_returns) if positive_returns else 0.0
    single_period_contribution_share = (
        max_single_period_return / total_positive_return if total_positive_return > 0 else 0.0
    )

    missing_required = [
        name
        for name, value in (
            ("Median Excess Return", median_excess_return),
            ("Beat SPY %", beat_spy_rate),
            (PROMOTION_COMPETITION_THRESHOLD_METRIC, ml_threshold_probability),
            ("Worst Period Return", worst_ml_period),
        )
        if value is None
    ]

    threshold_pass = False
    if ml_threshold_probability is not None and best_threshold_probability is not None:
        threshold_pass = ml_threshold_probability >= best_threshold_probability
    threshold_pass = threshold_pass or risk_adjusted_clearly_improved

    worst_period_pass = (
        worst_ml_period is not None
        and worst_existing_period is not None
        and worst_ml_period >= worst_existing_period - PROMOTION_WORST_PERIOD_TOLERANCE
    )

    criteria = {
        "Median excess return vs SPY > 0": median_excess_return is not None
        and median_excess_return > PROMOTION_MIN_MEDIAN_EXCESS_RETURN,
        f"Beat-SPY rate >= {PROMOTION_MIN_BEAT_SPY_RATE:.0%}": beat_spy_rate is not None
        and beat_spy_rate >= PROMOTION_MIN_BEAT_SPY_RATE,
        f"{PROMOTION_COMPETITION_THRESHOLD_METRIC} >= best existing strategy OR "
        "clearly improved Sharpe Ratio": threshold_pass,
        "Worst period not materially worse than best existing strategy": worst_period_pass,
        f"Tested across >= {PROMOTION_MIN_PERIODS_TESTED} periods": periods_tested
        >= PROMOTION_MIN_PERIODS_TESTED,
        f"No single period contributes > {PROMOTION_MAX_SINGLE_PERIOD_CONTRIBUTION_SHARE:.0%} "
        "of total positive return": single_period_contribution_share
        <= PROMOTION_MAX_SINGLE_PERIOD_CONTRIBUTION_SHARE,
    }

    all_pass = all(criteria.values()) and not missing_required
    hard_reject = (
        median_excess_return is not None
        and beat_spy_rate is not None
        and median_excess_return <= PROMOTION_REJECT_MEDIAN_EXCESS_RETURN
        and beat_spy_rate < PROMOTION_REJECT_BEAT_SPY_RATE
    )

    if missing_required:
        decision = RESEARCH_MORE
    elif all_pass:
        decision = PROMOTE
    elif hard_reject:
        decision = REJECT
    else:
        decision = RESEARCH_MORE

    reasons = [f"{'PASS' if passed else 'FAIL'} - {name}" for name, passed in criteria.items()]
    if missing_required:
        reasons.append(
            "Required metrics unavailable (not treated as zero): " + ", ".join(missing_required)
        )
    if decision == REJECT and hard_reject:
        reasons.append(
            "Hard REJECT: median excess return is not positive AND the beat-SPY rate is "
            f"below {PROMOTION_REJECT_BEAT_SPY_RATE:.0%}."
        )

    return PromotionResult(
        method=method_name,
        compared_against=best_existing_method_name,
        decision=decision,
        criteria=criteria,
        reasons=tuple(reasons),
    )


def build_promotion_table(promotion_results):
    """Flatten a list of `PromotionResult`s into one exportable table."""
    columns = ["Method", "Compared Against", "Decision", "Reasons"]
    if not promotion_results:
        return pd.DataFrame(columns=columns)
    rows = [
        {
            "Method": result.method,
            "Compared Against": result.compared_against,
            "Decision": result.decision,
            "Reasons": " | ".join(result.reasons),
        }
        for result in promotion_results
    ]
    return pd.DataFrame(rows, columns=columns)
