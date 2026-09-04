"""In-memory summaries and ZIP export for completed backtests."""

import io
import json
import re
import zipfile
from datetime import date, datetime

import pandas as pd


def build_test_summary(results):
    rows = []
    for result in results:
        requested_start = result.get("Requested Start", result["Start Date"])
        requested_end = result.get("Requested End", result["End Date"])
        actual_start = result.get("Actual Start", result["Start Date"])
        actual_end = result.get("Actual End", result["End Date"])
        rows.append(
            {
                "Algorithm": result["Algorithm"],
                "Test": result["Test"],
                "Requested Start": pd.Timestamp(requested_start).date(),
                "Requested End": pd.Timestamp(requested_end).date(),
                "Actual Start": pd.Timestamp(actual_start).date(),
                "Actual End": pd.Timestamp(actual_end).date(),
                "Strategy Return": result["Total Return"],
                "Benchmark Return": result["Benchmark Return"],
                "Excess Return": (
                    result["Total Return"] - result["Benchmark Return"]
                ),
                "Number of Trades": len(result["Trades"]),
            }
        )
    return pd.DataFrame(rows)


def build_comparison_summary(results):
    tests = build_test_summary(results)
    columns = [
        "Algorithm",
        "Average Return",
        "Median Return",
        "Average Benchmark Return",
        "Average Excess Return",
        "Win Rate vs Benchmark",
        "Best Test",
        "Worst Test",
        "Average Number of Trades",
    ]
    if tests.empty:
        return pd.DataFrame(columns=columns)

    rows = []
    for algorithm, group in tests.groupby("Algorithm", sort=False):
        best = group.loc[group["Strategy Return"].idxmax()]
        worst = group.loc[group["Strategy Return"].idxmin()]
        rows.append(
            {
                "Algorithm": algorithm,
                "Average Return": group["Strategy Return"].mean(),
                "Median Return": group["Strategy Return"].median(),
                "Average Benchmark Return": group["Benchmark Return"].mean(),
                "Average Excess Return": group["Excess Return"].mean(),
                "Win Rate vs Benchmark": (group["Excess Return"] > 0).mean(),
                "Best Test": f"Test {int(best['Test'])}: {best['Strategy Return']:+.2%}",
                "Worst Test": f"Test {int(worst['Test'])}: {worst['Strategy Return']:+.2%}",
                "Average Number of Trades": group["Number of Trades"].mean(),
            }
        )
    return pd.DataFrame(rows, columns=columns)


def _slug(value):
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def _json_default(value):
    if isinstance(value, (pd.Timestamp, datetime, date)):
        return value.isoformat()
    if hasattr(value, "item"):
        return value.item()
    raise TypeError(f"Cannot serialize {type(value).__name__}")


def build_complete_backtest_package(results, periods, settings):
    """Return one ZIP containing every completed strategy/test result."""
    package = io.BytesIO()
    comparison = build_comparison_summary(results)
    period_rows = []
    for index, (start, end) in enumerate(periods, start=1):
        completed = next(
            (result for result in results if result.get("Test") == index), None
        )
        period_rows.append(
            {
                "Test": index,
                "Requested Start": start.date(),
                "Requested End": end.date(),
                "Actual Start": (
                    pd.Timestamp(completed["Actual Start"]).date()
                    if completed and "Actual Start" in completed
                    else None
                ),
                "Actual End": (
                    pd.Timestamp(completed["Actual End"]).date()
                    if completed and "Actual End" in completed
                    else None
                ),
            }
        )
    period_table = pd.DataFrame(period_rows)

    with zipfile.ZipFile(package, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("comparison_summary.csv", comparison.to_csv(index=False))
        archive.writestr("periods.csv", period_table.to_csv(index=False))
        archive.writestr(
            "settings.json",
            json.dumps(settings, indent=2, default=_json_default, sort_keys=True),
        )

        for algorithm in settings["selected_strategies"]:
            algorithm_results = [
                result for result in results if result["Algorithm"] == algorithm
            ]
            folder = _slug(algorithm)
            summary = build_test_summary(algorithm_results)
            archive.writestr(f"{folder}/summary.csv", summary.to_csv(index=False))
            for result in algorithm_results:
                test_number = int(result["Test"])
                trades = pd.DataFrame(result["Trades"])
                holdings = pd.DataFrame(result["Final Holdings"])
                archive.writestr(
                    f"{folder}/test_{test_number:02d}_trades.csv",
                    trades.to_csv(index=False),
                )
                archive.writestr(
                    f"{folder}/test_{test_number:02d}_holdings.csv",
                    holdings.to_csv(index=False),
                )

    package.seek(0)
    return package.getvalue()
