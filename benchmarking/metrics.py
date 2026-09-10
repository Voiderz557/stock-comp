"""Per-period, aggregate, robustness, and competition metrics.

Every metric below is computed purely from the `run_portfolio_simulation`
result dicts already produced by `benchmarking.simulation` - no new
portfolio accounting, only arithmetic over the results.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from config import BACKTEST_STARTING_CASH

# Competition-focused return thresholds, exactly matching the earlier
# convention used elsewhere in this project: `>=` is inclusive, so an exact
# +20.00% return counts toward P(Return >= 20%).
COMPETITION_RETURN_THRESHOLDS = (0.10, 0.15, 0.20, 0.25, 0.30)

# Sharpe/Sortino need a minimum number of independent period observations to
# be even remotely meaningful - below this, both are reported as NaN rather
# than a misleadingly precise-looking number.
MIN_OBSERVATIONS_FOR_RATIOS = 5

# Trim this fraction off each tail before averaging, for a robustness check
# against a handful of extreme winners/losers dominating the plain average.
TRIMMED_MEAN_PROPORTION = 0.10

# If the trimmed mean return falls below this fraction of the plain average
# return (and the average return is itself positive), the method's apparent
# edge is flagged as concentrated in a small number of extreme periods.
EXTREME_WINNER_RELATIVE_DROP_THRESHOLD = 0.5

TRADING_DAYS_PER_YEAR = 365.25


def compute_max_drawdown(portfolio_history):
    """Deepest peak-to-trough decline in one period's portfolio value path."""
    values = np.array(
        [row["Portfolio Value"] for row in portfolio_history], dtype=float
    )
    if len(values) == 0:
        return 0.0
    running_max = np.maximum.accumulate(values)
    safe_peak = np.where(running_max > 0, running_max, np.nan)
    drawdowns = values / safe_peak - 1
    finite = drawdowns[np.isfinite(drawdowns)]
    return float(finite.min()) if len(finite) else 0.0


def compute_turnover(result, starting_cash=None):
    """Total traded dollar notional / starting cash, for one period."""
    starting_cash = starting_cash or result.get("Starting Value", BACKTEST_STARTING_CASH)
    trades = result.get("Trades", [])
    traded_notional = sum(trade["Shares"] * trade["Price"] for trade in trades)
    if not starting_cash:
        return 0.0
    return float(traded_notional / starting_cash)


def build_period_row(result, test_number):
    """One row of `benchmarking.metrics.build_period_results_table`."""
    return {
        "Method": result["Method"],
        "Test": test_number,
        "Requested Start": pd.Timestamp(result["Requested Start"]).date(),
        "Requested End": pd.Timestamp(result["Requested End"]).date(),
        "Actual Start": pd.Timestamp(result["Actual Start"]),
        "Actual End": pd.Timestamp(result["Actual End"]),
        "Total Return": float(result["Total Return"]),
        "Benchmark Return": float(result["Benchmark Return"]),
        "Excess Return": float(result["Total Return"] - result["Benchmark Return"]),
        "Number of Trades": len(result["Trades"]),
        "Max Drawdown": compute_max_drawdown(result["Portfolio History"]),
        "Turnover": compute_turnover(result),
    }


def build_period_results_table(period_rows):
    columns = [
        "Method",
        "Test",
        "Requested Start",
        "Requested End",
        "Actual Start",
        "Actual End",
        "Total Return",
        "Benchmark Return",
        "Excess Return",
        "Number of Trades",
        "Max Drawdown",
        "Turnover",
    ]
    if not period_rows:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(period_rows, columns=columns)


def _trimmed_mean(values, proportion):
    sorted_values = np.sort(values)
    n = len(sorted_values)
    cut = int(np.floor(n * proportion))
    if n - 2 * cut <= 0:
        return float(np.mean(sorted_values))
    return float(sorted_values[cut : n - cut].mean())


def _is_extreme_winner_dependent(average_return, trimmed_mean_return):
    """True if a positive average return mostly comes from a few extreme periods."""
    if average_return is None or average_return <= 0:
        return False
    return trimmed_mean_return < average_return * EXTREME_WINNER_RELATIVE_DROP_THRESHOLD


def compute_aggregate_metrics(period_rows):
    """Every requested metric, aggregated across one method's period rows.

    `period_rows` is a list of `build_period_row(...)` dicts for ONE method,
    all drawn from the exact same set of periods used for every other
    method (see `benchmarking.runner`).
    """
    if not period_rows:
        return {}

    returns = np.array([row["Total Return"] for row in period_rows], dtype=float)
    excess_returns = np.array([row["Excess Return"] for row in period_rows], dtype=float)
    n = len(returns)

    chained_return = float(np.prod(1 + returns) - 1)
    total_days = sum(
        (row["Actual End"] - row["Actual Start"]).days for row in period_rows
    )
    annualized_return = (
        float((1 + chained_return) ** (TRADING_DAYS_PER_YEAR / total_days) - 1)
        if total_days > 0
        else np.nan
    )

    average_return = float(returns.mean())
    median_return = float(np.median(returns))
    volatility = float(returns.std(ddof=1)) if n > 1 else np.nan

    def _usable_deviation(value):
        return value is not None and np.isfinite(value) and abs(value) > 1e-15

    sharpe_ratio = (
        float(average_return / volatility)
        if n >= MIN_OBSERVATIONS_FOR_RATIOS and _usable_deviation(volatility)
        else np.nan
    )
    downside = returns[returns < 0]
    downside_deviation = float(downside.std(ddof=1)) if len(downside) > 1 else np.nan
    sortino_ratio = (
        float(average_return / downside_deviation)
        if n >= MIN_OBSERVATIONS_FOR_RATIOS and _usable_deviation(downside_deviation)
        else np.nan
    )

    best_index = int(np.argmax(returns))
    worst_index = int(np.argmin(returns))
    trimmed_mean_return = _trimmed_mean(returns, TRIMMED_MEAN_PROPORTION)

    metrics = {
        "Periods Tested": n,
        "Total Return": chained_return,
        "Annualized Return": annualized_return,
        "Average Return": average_return,
        "Median Return": median_return,
        "Average Excess Return": float(excess_returns.mean()),
        "Median Excess Return": float(np.median(excess_returns)),
        "Beat SPY %": float((excess_returns > 0).mean()),
        "Positive Period %": float((returns > 0).mean()),
        "Max Drawdown": min(row["Max Drawdown"] for row in period_rows),
        "Volatility": volatility,
        "Sharpe Ratio": sharpe_ratio,
        "Sortino Ratio": sortino_ratio,
        "Trade Count": float(np.mean([row["Number of Trades"] for row in period_rows])),
        "Turnover": float(np.mean([row["Turnover"] for row in period_rows])),
        "Best Period Return": float(returns[best_index]),
        "Best Period Test": period_rows[best_index]["Test"],
        "Worst Period Return": float(returns[worst_index]),
        "Worst Period Test": period_rows[worst_index]["Test"],
        "Trimmed Mean Return": trimmed_mean_return,
        "Percentile 10": float(np.percentile(returns, 10)),
        "Percentile 25": float(np.percentile(returns, 25)),
        "Percentile 75": float(np.percentile(returns, 75)),
        "Percentile 90": float(np.percentile(returns, 90)),
    }
    for threshold in COMPETITION_RETURN_THRESHOLDS:
        metrics[f"P(Return >= {int(round(threshold * 100))}%)"] = float(
            (returns >= threshold).mean()
        )
    metrics["Extreme Winner Dependent"] = _is_extreme_winner_dependent(
        average_return, trimmed_mean_return
    )
    return metrics


def build_aggregate_table(period_table):
    """One row per `Method`, from `build_period_results_table`'s output."""
    if period_table.empty:
        return pd.DataFrame(columns=["Method"])
    rows = []
    for method, group in period_table.groupby("Method", sort=False):
        period_rows = group.to_dict("records")
        rows.append({"Method": method, **compute_aggregate_metrics(period_rows)})
    return pd.DataFrame(rows)
