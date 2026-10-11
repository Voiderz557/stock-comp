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
    "regime_path.csv",
    "candidate_attribution.csv",
    "position_attribution.csv",
    "exposure_history.csv",
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
    """Return ZIP bytes with summary, audit, and attribution artifacts."""
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
        archive.writestr("regime_path.csv", benchmark_result.regime_path_table.to_csv(index=False))
        archive.writestr(
            "candidate_attribution.csv",
            benchmark_result.candidate_attribution_table.to_csv(index=False),
        )
        archive.writestr(
            "position_attribution.csv",
            benchmark_result.position_attribution_table.to_csv(index=False),
        )
        archive.writestr("exposure_history.csv", benchmark_result.exposure_table.to_csv(index=False))

    buffer.seek(0)
    return buffer.getvalue()
