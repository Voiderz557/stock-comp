"""Single ZIP export of every benchmark artifact."""

from __future__ import annotations

import io
import json
import zipfile
from datetime import date, datetime

import numpy as np
import pandas as pd

EXPORT_FILENAMES = (
    "benchmark_summary.csv",
    "period_results.csv",
    "ml_fold_metrics.csv",
    "feature_stability.csv",
    "leakage_audit.csv",
    "promotion_decision.csv",
    "benchmark_config.json",
)


def _json_default(value):
    if isinstance(value, (pd.Timestamp, datetime, date)):
        return value.isoformat()
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return None if not np.isfinite(value) else float(value)
    if hasattr(value, "item"):
        return value.item()
    raise TypeError(f"Cannot serialize {type(value).__name__}")


def build_leakage_audit_table(leakage_audit):
    checks = leakage_audit.get("Checks", []) if leakage_audit else []
    columns = ["Check", "Passed", "Detail"]
    if not checks:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(checks, columns=columns)


def build_benchmark_zip(benchmark_result):
    """Return ZIP bytes with the 7 required benchmark export files."""
    buffer = io.BytesIO()
    leakage_table = build_leakage_audit_table(benchmark_result.leakage_audit)

    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "benchmark_summary.csv", benchmark_result.aggregate_table.to_csv(index=False)
        )
        archive.writestr(
            "period_results.csv", benchmark_result.period_table.to_csv(index=False)
        )
        archive.writestr(
            "ml_fold_metrics.csv", benchmark_result.fold_metrics_table.to_csv(index=False)
        )
        archive.writestr(
            "feature_stability.csv", benchmark_result.feature_stability_table.to_csv(index=False)
        )
        archive.writestr("leakage_audit.csv", leakage_table.to_csv(index=False))
        archive.writestr(
            "promotion_decision.csv", benchmark_result.promotion_table.to_csv(index=False)
        )
        archive.writestr(
            "benchmark_config.json",
            json.dumps(benchmark_result.config, indent=2, default=_json_default, sort_keys=True),
        )

    buffer.seek(0)
    return buffer.getvalue()
