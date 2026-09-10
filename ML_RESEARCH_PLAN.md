# ML Research Plan

This document describes the `ml/` package: a research-only foundation for
building historical, point-in-time-safe feature datasets and evaluating
simple classification models that **rank** stocks. It does **not** place
trades, is not wired into paper trading or the competition dashboard, and
does not change any existing strategy, the backtesting engine, or portfolio
rules.

## 1. What problem this solves

Every strategy in `strategies/` is a small, fixed set of hand-picked factors
combined with hand-picked weights (Baseline, Momentum V2, Mean Reversion
V1). That is intentional and stays unchanged. The ML layer exists to answer
a different, exploratory question: **across a much larger feature set (price
trend, momentum, relative strength, breakout, volume, volatility, mean
reversion, market regime, and the existing strategies' own outputs), can a
simple, transparent classifier meaningfully separate stocks that go on to
beat a benchmark from stocks that don't?**

This is deliberately framed as *research*, not *production*:

- Nothing here is called from `app/competition_dashboard.py`, `backtesting/engine.py`,
  or `paper_trading/`.
- No trade is ever placed based on a model's output.
- Only two intentionally simple, interpretable models are provided
  (Logistic Regression, Random Forest) - no deep learning, no gradient
  boosting, no reinforcement learning.

## 2. Why ranking, not direct trading

The framework ranks stocks by *predicted probability of beating a
benchmark* over a fixed horizon, then reports what would have happened if
you had bought the top-N ranked stocks each rebalance date. This is
intentional for two reasons:

1. **Classification accuracy is not the same as profitability.** A model
   can be 55% accurate and still lose money after costs, or be less
   "accurate" but produce a great top-N portfolio (accuracy near a 50/50
   decision boundary says little; the *ranking* at the extremes is what a
   long-only strategy actually uses). The evaluation framework reports
   accuracy/precision/recall/F1/ROC AUC *and*, separately, top-N trading
   metrics, and explicitly compares the model's top-N picks against a
   random-N baseline and an existing strategy's own ranking - so no claim
   of profitability rests on classification accuracy alone.
2. **Ranking is a much smaller, safer step toward eventually informing
   trading decisions** than directly emitting BUY/SELL orders. It slots
   naturally next to the existing strategies (which already rank candidates
   via `rank_key`) without requiring any changes to the backtesting engine,
   portfolio rules, or competition constraints.

## 3. Package layout

```
ml/
    __init__.py
    features.py     Point-in-time feature computation for one ticker/date.
    labels.py        Forward-return labels and classification targets.
    validation.py    Chronological walk-forward fold generation + leakage guards.
    dataset.py       build_feature_dataset(...) API, caching, export, quality report.
    models.py        Simple sklearn Pipelines (Logistic Regression, Random Forest).
    evaluation.py    Walk-forward evaluation, metrics, top-N ranking, importance.

app/ml_research_ui.py   Streamlit research UI ("Research Only - Not Used for Live Trading").
tests/test_ml_*.py      Test suite (70 tests) for the package above.
tests/ml_fixtures.py    Shared synthetic-data helpers used by those tests.
```

## 4. Feature definitions

All features are computed by `ml/features.py::compute_feature_row(...)` from
a price-history DataFrame that has already been truncated to rows at or
before the evaluation date `T` (see [Section 6](#6-no-look-ahead-protections)).
`REQUIRED_HISTORY_DAYS` (200 trading rows) is the longest window any feature,
the market-regime module, or any *registered* strategy needs - it is
computed automatically from those pieces, so it grows on its own if a future
strategy needs more history.

| Group | Feature | Definition |
|---|---|---|
| Price/Trend | `Price` | Latest close at/before T |
| | `Distance From MA20/50/200` | `(price / MA_N) - 1` |
| | `MA20/50 Slope` | Fractional change in the moving average over the last 5 trading days: `(MA_now - MA_5_days_ago) / abs(MA_5_days_ago)` |
| Momentum | `Momentum 5D/20D/60D/120D` | `(close_T / close_(T-N)) - 1` |
| Relative Strength | `Relative Strength 20D/60D SPY` / `QQQ` | `stock_momentum_N - benchmark_momentum_N` |
| Breakout | `Breakout Distance 20D/60D` | `(price / prior_N_day_high) - 1`, where the prior high is the max close over the **N rows strictly before** T (today's own close is excluded) |
| Volume | `Volume Ratio 5D 20D` | 5-day average volume / 20-day average volume |
| | `Volume Trend 20D` | (average volume, most recent 10 days) / (average volume, prior 10 days) − 1 |
| Volatility | `Volatility 20D/60D` | Standard deviation of daily % returns over the window |
| Mean Reversion | `RSI14` | Standard 14-period RSI (reuses `strategies.mean_reversion_v1.calculate_rsi` - not duplicated) |
| | `Price Zscore MA20` | `(price - mean_20d) / std_20d` (population std; 0.0 for a flat/zero-variance window) |
| Market Regime | `Regime` (categorical) | One of `STRONG_UPTREND` / `WEAK_UPTREND` / `SIDEWAYS` / `DOWNTREND` / `HIGH_VOLATILITY`, from the **existing, unmodified** `market.regime.classify_market_regime` |
| | `Regime Confidence`, `Regime SPY/QQQ Momentum 20D/60D`, `Regime Market Volatility 20D`, `Regime SPY/QQQ Above MA50/MA200` | Supporting metrics from the same regime call |
| Strategy Features | `Strategy Score: <name>`, `Strategy Signal: <name>` (one pair per strategy in `strategies.registry.available_strategy_names()`) | Calls each strategy's own `analyze(ticker, historical_data)` directly (no logic duplicated); `Signal` is numerically encoded `BUY=1.0, WAIT=0.0, AVOID=-1.0`. If a strategy needs more history than is available for that row, both are `NaN` (handled by the models' imputers) |

`ml.features.numeric_feature_columns()` gives a best-effort static list of
the non-strategy numeric feature names for documentation/quick reference;
`ml.models.infer_feature_columns(dataset)` is the authoritative, dataframe-
driven split (it adapts automatically to however many strategies are
currently registered).

## 5. Label definitions

Computed by `ml/labels.py::compute_labels(...)`, using the **same** ticker's
future closes plus SPY/QQQ future closes (the only module in `ml/` allowed
to read rows dated after `T`):

- `Forward Return 5D / 20D / 60D` = `close_(T+H) / close_T - 1`, where `T+H`
  is `H` **trading rows** after T (not calendar days). `NaN` if fewer than
  `H` future rows exist.
- `beats_SPY_20D` = `Forward Return 20D(stock) > Forward Return 20D(SPY)`
- `beats_QQQ_20D` = `Forward Return 20D(stock) > Forward Return 20D(QQQ)`
- `positive_20D` = `Forward Return 20D(stock) > 0`
- `return_ge_5pct_20D` = `Forward Return 20D(stock) >= 0.05`

Any of the four classification targets is `NaN` (not `False`) whenever a
return it depends on is unavailable, so "insufficient future data" is never
silently conflated with "no". `ml.dataset.get_supervised_subset(dataset,
target_column)` drops exactly those rows before training.

`label_horizon` (passed to `build_feature_dataset`) additionally selects
which horizon feeds a second, generically-named set of columns (`Forward
Return {H}D`, `Beats SPY {H}D`, etc.) - the four fixed `_20D`-suffixed
columns above are always computed in addition, regardless of `label_horizon`.

## 6. No-look-ahead protections

This was the most important constraint, and is enforced at three levels:

1. **`ml.features.truncate_to_as_of(data, as_of_date)`** is the single,
   explicit helper that cuts price data to rows `<= as_of_date`. Every
   feature (including the regime call and every strategy's `analyze()`
   call) only ever sees data that has gone through this cut.
2. **`ml.labels` is the only module allowed to read future rows.** Nothing
   in `ml.features` imports `ml.labels`, and nothing in `ml.labels` computes
   a feature - the asymmetry is structural, not just a convention.
3. **`ml.dataset.build_feature_dataset`** is the only place that decides
   what "as of T" means for a given ticker/date: it truncates each ticker's
   and each benchmark's history independently per evaluation date, computes
   the market regime **once per date** from the truncated SPY/QQQ data
   (reused across every ticker that date for consistency and speed), and
   only then calls `ml.labels.compute_labels(...)` with the **untruncated**
   ticker/benchmark closes purely to look up future prices.
4. **Point-in-time universe membership** reuses the existing, unmodified
   `data.historical_universe.get_historical_universe`/`get_backtest_tickers`
   - the same functions the backtesting engine itself uses - rather than
   reimplementing membership logic. Passing an explicit `universe=[...]`
   list opts out of this (documented, deliberate) and uses that fixed list
   for every date instead.
5. **Breakout features exclude the current row** from their own "prior
   high" window by construction (`_prior_high` looks at the `N` rows
   strictly before the current one).

Tests in `tests/test_ml_features.py` and `tests/test_ml_dataset.py` prove
this directly: they take an identical price history, inject a large price
shock *after* the evaluation date, and assert every feature (and, in the
dataset-level test, every row) is byte-for-byte identical to the un-shocked
version. `tests/test_ml_labels.py` proves the opposite for labels: a shock
placed inside a forward-return window *does* change that label, while a
shock placed outside a shorter window (e.g. 5D) does not.

## 7. Walk-forward validation design

`ml/validation.py::generate_walk_forward_folds(start_date, end_date,
min_train_days, validation_days, step_days, window_mode)` builds a list of
`WalkForwardFold(train_start, train_end, validation_start, validation_end)`
tuples with two supported window modes:

- **`"expanding"`** (default): `train_start` stays fixed; the training
  window grows by `step_days` every fold (e.g. train
  `2022-01-01..2023-12-31`, then `2022-01-01..2024-03-31`, ...).
- **`"rolling"`**: the training window keeps a fixed length and slides
  forward by `step_days` every fold, so old data eventually ages out.

Every fold satisfies `train_end == validation_start - 1 day` by
construction, so `assert_no_temporal_leakage(train_df, validation_df)` (used
before every fit in `ml.evaluation.run_walk_forward_evaluation`) can never
find an overlap in normal operation - it exists as an explicit, independent
audit check anyway. No `train_test_split` or any other random split is used
anywhere in this package.

## 8. Model pipelines

`ml/models.py` provides exactly two `sklearn.pipeline.Pipeline`s, both
built from `ml.models.infer_feature_columns(dataset)` (which excludes
`Ticker`/`Date`/`As Of Trading Date` and every known label column):

- **Logistic Regression**: numeric features → `SimpleImputer(median)` →
  `StandardScaler` → `LogisticRegression`; the `Regime` categorical column →
  `SimpleImputer(most_frequent)` → `OneHotEncoder`.
- **Random Forest**: identical preprocessing, minus scaling (trees don't
  need it) → `RandomForestClassifier(n_estimators=300, max_depth=6)`.

Both use `random_state=42` for determinism. `ml.models.extract_feature_importance`
returns coefficients (Logistic Regression) or `feature_importances_`
(Random Forest) as a simple, sorted `Feature` / `Coefficient`-or-`Importance`
table - no SHAP.

## 9. Evaluation metrics

For every fold, `ml.evaluation.run_walk_forward_evaluation` fits a **fresh**
pipeline on that fold's training rows only, then reports:

- **Classification**: Accuracy, Precision, Recall, F1, ROC AUC (`NaN` if the
  validation fold has only one class present).
- **Trading-relevant (top-N)**: for N in `{5, 10, 20}`, on each rebalance
  date rank validation rows by predicted probability, take the top N,
  and average (across all dates in the fold) their forward 20D return,
  excess return vs. a benchmark column (if supplied), hit rate vs.
  benchmark, and positive-return rate. The same metrics are computed for a
  **random-N baseline** (seeded, deterministic) and, if a
  `Strategy Score: <name>` column is passed as `strategy_score_column`, for
  that **existing strategy's own ranking** - reusing the feature column
  already in the dataset rather than recomputing anything.

`ml.evaluation.aggregate_fold_metrics` averages both metric groups across
every fold that was evaluated.

## 10. Data quality reporting

`ml.dataset.build_quality_report(dataset)` reports row/ticker counts, date
range, missing-value counts, class balance (on `beats_SPY_20D` by default),
and - via `dataset.attrs` set by `build_feature_dataset` - the number of
rows excluded for insufficient history, the number of rows kept but with a
`NaN` label due to insufficient future data, and every recorded per-
ticker/date failure with its reason. This is exportable as JSON via
`ml.dataset.export_quality_report_json`.

## 11. Model limitations

- Both models are intentionally simple; they are not tuned, and no
  hyperparameter search is performed anywhere (including against the final
  validation period, per the constraints in this task).
- The universe defaults to today's Nasdaq-100 test list filtered by
  historical snapshot dates - the same survivorship-bias caveat that
  already applies to the existing backtesting engine applies here.
- Forward returns are close-to-close, not open-to-open; the existing
  backtesting engine executes at the next day's Open, so live trading
  economics would differ somewhat from what these labels measure.
- A stock more than `DEFAULT_MAX_STALE_TRADING_GAP_DAYS` (10) calendar days
  stale relative to the nominal evaluation date is skipped for that date,
  rather than reusing an old price - this reduces but does not eliminate
  the effects of gaps in historical data for delisted/illiquid tickers.
- Dataset-level caching (`cache_path`) persists the *final* built
  DataFrame, not intermediate per-ticker feature computations; rebuilding
  with different parameters always recomputes from scratch.

## 12. Launching the research UI

```
python -m streamlit run app/ml_research_ui.py
```

The page is explicitly labeled **"Research Only - Not Used for Live
Trading"**. It lets you: choose a date range, label horizon, and rebalance
frequency; build or rebuild the dataset; inspect its shape, missing values,
and quality report; export it as CSV; pick a model and walk-forward
settings; run validation; and view per-fold metrics, aggregate metrics,
top-N ranking performance (vs. random and vs. an existing strategy), and
feature importance.
