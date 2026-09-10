"""Research-only ML foundation for the stock-comp project.

This package builds historical, point-in-time-safe feature datasets and
provides a walk-forward evaluation framework for simple classification
models that RANK stocks (probability of beating a benchmark), rather than
directly generating trades.

Nothing in this package is wired into paper trading, the competition
dashboard, or the backtesting engine. See `ML_RESEARCH_PLAN.md` at the
project root for the full design write-up, and `app/ml_research_ui.py` for
the Streamlit research UI ("Research Only - Not Used for Live Trading").

Modules
-------
features.py    Point-in-time feature computation for one ticker/date.
labels.py      Forward-return labels and classification targets.
validation.py  Chronological walk-forward fold generation and leakage guards.
dataset.py     `build_feature_dataset(...)` API, caching, export, quality report.
models.py      Simple sklearn Pipelines (Logistic Regression, Random Forest).
evaluation.py  Walk-forward evaluation, metrics, top-N ranking, importance.
"""
