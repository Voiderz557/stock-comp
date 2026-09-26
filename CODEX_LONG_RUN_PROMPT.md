# Next Stock-Comp Mission — Copy/Paste Prompt

Continue developing the `stock-comp` project from its current state and complete
the next major reliability and research phase. Work autonomously through all safe
steps below. This should be a substantial session, not a quick five-minute audit.

Start with a realistic estimate based on the actual work. After inspecting the
repository, revise the estimate using measured test and benchmark timings. During
long operations, report the active phase, elapsed time, and estimated remaining
time. Do not stop merely because the original estimate expires.

## Current verified state

- Work is merged into `main` and pushed to GitHub.
- Current merge commit should be `96cd0a2`; verify rather than assuming.
- The last full test run passed: 399 tests and 31 subtests.
- The daily assistant, desktop packaging, manual account setup, saved plans,
  strategy framework, market regime system, ML research framework, benchmarking
  framework, leakage controls, and Cursor/Codex coordinator are implemented.
- A diagnostic five-month benchmark completed all 13 methods but was correctly
  marked INVALID because historical price coverage was incomplete.
- An isolated recovery candidate repaired 83 affected histories without changing
  the production cache.
- Six historical constituents remained unresolved: ANSS, ATVI, EA, SGEN, SPLK,
  and WBA. Recalculate this list before relying on it.
- The production portfolio database, production cache, saved plans, working
  executable builds, and user runtime data must remain untouched.

## Primary objective

Determine whether we can obtain a complete, trustworthy, point-in-time historical
dataset for the 2023–2025 competition research period. If the dataset becomes
valid, run a carefully staged benchmark of every existing strategy and ML ranking.
If it remains invalid, finish all useful engineering work around the blocker and
produce an exact data-acquisition plan. Never fabricate data or present an invalid
benchmark as meaningful.

## Step 1 — inspect and protect the current state

1. Read any project `AGENTS.md` instructions plus `README.md`,
   `VALIDATION_STATUS.md`, `DEVELOPMENT_PLAN.md`, and `ML_RESEARCH_PLAN.md`.
2. Inspect the current branch, HEAD, upstream, Git status, and ignored runtime
   paths.
3. Preserve all unrelated work. Never reset, stash, discard, or overwrite it.
4. Locate the newest isolated recovery directory and verify that its recorded
   source-cache checksums still match the production cache.
5. Run focused coverage/recovery tests before making changes.

## Step 2 — resolve the historical-data blockers

For ANSS, ATVI, EA, SGEN, SPLK, and WBA:

1. Verify the exact historical Nasdaq-100 membership interval.
2. Verify listing, ticker-change, acquisition, delisting, and final-trading dates
   using primary sources such as SEC filings, Nasdaq notices, and official company
   announcements.
3. Inspect current symbol-routing and listing-boundary code before downloading.
4. Inspect failure-cache entries and do not repeat a request that already failed
   for the same relevant trading sessions during this run.
5. Do not treat a weekend failure, pre-listing request, or generic Yahoo error as
   proof that the complete historical series is unavailable.
6. Search for a lawful and attributable source of historical daily OHLCV data.
   Prefer free official/exchange/company sources where practical. Do not purchase
   data, create a paid API fallback, or configure an API key without explicit user
   approval.
7. Download only into a new isolated candidate directory. Never write into the
   production cache.
8. Record provider, provider symbol, retrieval time, requested dates, returned
   dates, adjustment convention, source URL, and file checksum.
9. Preserve historical constituents even if data remains unavailable.
10. Do not use current universe membership as a historical substitute.

If a trustworthy source cannot be accessed, create a source-neutral import path
for user-supplied CSV or Parquet data. It must validate schema, ticker, required
coverage, duplicate dates, OHLC relationships, numeric values, adjustment
convention, provenance, and checksum. Do not silently promote imported data.

## Step 3 — audit price integrity and corporate actions

For all recovered candidate histories:

1. Confirm sorted, unique trading dates and valid OHLCV values.
2. Detect missing required market sessions using the SPY trading calendar.
3. Check split and dividend consistency.
4. Compare refreshed and old overlapping adjusted closes and flag unexplained
   ratio changes.
5. Determine whether every series uses a compatible adjusted-price convention.
6. Never splice adjusted and unadjusted rows together.
7. Verify symbol continuity for renamed companies.
8. Document acquisition consideration for delisted names, including whether it was
   cash, stock, or mixed consideration.
9. Distinguish the final market close from actual acquisition/delisting proceeds.
10. Audit whether the historical $5 eligibility rule uses the intended price
    convention.

Do not change portfolio accounting or invent delisting proceeds merely to make a
test pass. If terminal-value handling is not supportable from verified data, mark
the affected benchmark periods invalid.

## Step 4 — fix demonstrated reliability problems

Implement only evidence-backed fixes. Check:

- observation date versus feature warmup date;
- universe-membership date versus price-history date;
- range-specific provider failure caching;
- pre-listing request prevention;
- symbol-routing boundaries;
- recovery resume behavior;
- source provenance and checksums;
- candidate-cache selection in validation tools;
- progress reporting and timeouts;
- fail-closed coverage and promotion decisions;
- JSON/CSV/ZIP export of missing-data diagnostics.

Add regression tests for each defect. Do not modify strategy formulas, signals,
competition constraints, backtest accounting, portfolio rules, random-period
generation, or model hyperparameters.

## Step 5 — prove every algorithm still behaves correctly

Run and, where missing, add focused tests proving:

- all six registered strategies remain unchanged and resolvable;
- Regime Switching references only available strategies;
- required history and benchmark-context requirements are preserved;
- each strategy uses only rows available at its evaluation timestamp;
- breakout prior highs exclude the current row;
- relative strength uses matching historical benchmark windows;
- volatility is the standard deviation of daily returns;
- annualized volatility uses daily volatility multiplied by square root of 252
  and is labeled separately;
- volatility penalties behave only as each strategy defines;
- exactly 20% counts as return greater than or equal to 20%;
- existing regression outputs remain unchanged.

Do not tune weights from these results. This phase verifies implementation, not
strategy optimization.

## Step 6 — enforce benchmark validity

The benchmark may proceed only if:

- point-in-time universe coverage is complete;
- required stock, SPY, and QQQ sessions are available;
- warmup rows do not become observation rows;
- rows without complete future labels are excluded;
- every training label is observable by the training cutoff;
- training dates strictly precede validation dates;
- train and validation ticker/date rows do not overlap;
- preprocessing and fitting happen only on training data;
- future-price changes cannot alter earlier features or predictions;
- historical regime and strategy features use historical-only inputs;
- all methods use identical periods and price snapshots;
- every requested method completes every common period;
- missing metrics remain unavailable rather than becoming zero;
- failed or unperformable leakage checks invalidate the benchmark;
- invalid benchmarks cannot produce PROMOTE.

If any required check fails, stop before the large benchmark. Do not report a
leaderboard or winner. Produce coverage and leakage reports explaining the block.

## Step 7 — run a staged benchmark only if valid

If all validity gates pass:

1. Run one instrumented real five-month period first.
2. Show timings for data loading, feature construction, fitting, each simulation,
   leakage audit, metrics, and export.
3. Confirm every method completed the same period.
4. Run 20 deterministic historical tests using a recorded seed.
5. Inspect coverage, errors, leakage, reconciliation, and outlier dependence.
6. Estimate 50-test and 100-test runtimes from the measured 20-test run.
7. Continue to 50 and then 100 only while each stage remains valid.
8. Use five months as the main horizon and six months only as a labeled sensitivity.
9. Reuse identical ML feature construction where safe, while fitting each fold's
   preprocessing and model on training data only.

Compare on identical periods:

- Baseline
- Momentum V2
- Aggressive Momentum V1
- Relative Strength Momentum V1
- Breakout Volume V1
- Mean Reversion V1
- Regime Switching
- Logistic Regression Top 5, Top 10, and Top 20
- Random Forest Top 5, Top 10, and Top 20

Report returns, excess returns, beat-SPY rate, positive-period rate, drawdown,
daily and annualized volatility, Sharpe, Sortino, turnover, trades, percentiles,
trimmed mean, exact 10/15/20/25/30% thresholds, outlier concentration, fold
stability, feature-importance stability, and promotion reasoning.

Do not claim profitability from classification accuracy. Label overlapping random
periods as descriptive stress tests, not independent statistical evidence.

## Step 8 — protect the daily assistant

Confirm that:

- the real portfolio database was not opened for writes;
- account snapshots, plans, actions, and history remain untouched;
- known-working executables and build folders remain untouched;
- ML remains disconnected from trade execution;
- no real or automatic paper trades occur.

Use temporary databases for dashboard smoke tests. Rebuild the Windows app only if
source changes directly affect it, and then use a separate output directory.

## Step 9 — test and clean up

1. Run focused tests while implementing.
2. Run `python -m pytest` at the end.
3. Run benchmark/research UI smoke checks with temporary data.
4. Verify benchmark ZIP contents and JSON serialization.
5. Confirm databases, caches, exports, logs, worktrees, secrets, and builds are
   ignored by Git.
6. Do not delete real runtime data.
7. Remove only accidental temporary artifacts created during this task.
8. Run `git diff --check` and report concise Git status.

If the result is a coherent checkpoint and tests pass, create a local commit. Do
not push, merge, open a pull request, publish, or release without a later explicit
request.

## Final response

Report actual elapsed time and phase timings, files changed, data provenance,
coverage before and after, unresolved symbols and ranges, corporate-action
findings, bugs fixed, regression tests, full pytest result, smoke-test result,
benchmark validity, valid results if available, report paths, checkpoint commit,
Git status, and the single best next action.

Do not stop after restating this plan. Execute until complete or until the remaining
blocker genuinely requires payment, credentials, explicit approval, or unavailable
external data.
