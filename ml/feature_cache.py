"""Reusable point-in-time feature cache for one process / benchmark run.

Caches trailing-indicator tables and completed feature rows keyed by
ticker, as-of date, and a cheap fingerprint of the history used. Fitted
models are never stored here. Call `reset_feature_cache()` between
unrelated datasets; a future-price shock that does not change rows at or
before the as-of date keeps the same key on purpose.
"""

from __future__ import annotations

from collections import Counter
from threading import Lock

import pandas as pd


_CACHE = None


def _new_cache():
    return {
        "indicator_tables": {},
        "tables_by_ticker": {},
        "feature_rows": {},
        "strategy_features": {},
        "stats": Counter(),
        "lock": Lock(),
    }


def get_feature_cache():
    global _CACHE
    if _CACHE is None:
        _CACHE = _new_cache()
    return _CACHE


def reset_feature_cache():
    global _CACHE
    _CACHE = _new_cache()
    return _CACHE


def history_fingerprint(frame, column="Close"):
    if frame is None or getattr(frame, "empty", True):
        return (None, 0, None, None)
    series = frame[column] if column in frame.columns else frame.iloc[:, 0]
    series = series.dropna()
    if series.empty:
        return (None, 0, None, None)
    values = series.astype(float)
    last = values.iloc[-1]
    return (
        pd.Timestamp(series.index[-1]),
        int(len(series)),
        None if pd.isna(last) else float(last),
        None if values.empty else float(values.sum()),
    )


def cache_stats():
    return dict(get_feature_cache()["stats"])
