"""ML vs. rule-based-strategy benchmarking framework - research only.

This package answers one question: do the `ml` package's ranking models
(Logistic Regression, Random Forest) outperform the existing rule-based
strategies enough to justify future paper-trading integration? Nothing here
places a real or paper trade, and nothing here is wired into
`paper_trading/` or `app/competition_dashboard.py`.

Modules
-------
simulation.py     Portfolio simulation for an arbitrary strategy-like object
                   (reuses `backtesting.engine`'s accounting functions
                   unmodified); `MLRankingStrategy` and `RegimeSwitchingStrategy`.
ml_training.py    Walk-forward-safe per-period ML model training.
metrics.py        Per-period, aggregate, robustness, and competition metrics.
stability.py      ML feature-importance / prediction / class-balance stability.
leakage_audit.py  Automated no-look-ahead audit; invalidates the benchmark on failure.
promotion.py      Transparent PROMOTE / RESEARCH_MORE / REJECT recommendation.
runner.py         `run_benchmark(...)`: top-level orchestration.
export.py         Single ZIP export of every benchmark artifact.
"""
