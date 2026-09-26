# Validation checkpoint — not a strategy leaderboard

## Scope and preserved systems

This pass tests the daily assistant, strategy contracts, and research benchmark.
No strategy formula, model hyperparameter, competition limit, trading engine,
portfolio ledger, production price cache, or packaged executable was changed.
No commit, push, real trade, model optimization, or 100-period study was performed.

## Findings fixed

- Forward training labels could mature inside validation even when feature dates
  preceded validation. Training input prices now end at the fold cutoff; explicit
  target-availability timestamps also purge unavailable targets in research folds.
  Rebuild old datasets; do not trust old ML benchmark scores as out-of-sample evidence.
- Explicit point-in-time `None` in the leakage audit was replaced by the small
  ticker probe list. Dataset reconstruction now preserves the true training universe.
- Supplied price dictionaries previously bypassed coverage validation. Coverage
  is now audited against historical membership and benchmark sessions, including
  training observation ranges (not indicator warmup membership).
- A partially completed set of method/period pairs can no longer be marked valid.
- Overlapping random experiments no longer produce compounded total/annualized
  returns, nor qualify as independent confirmation for promotion.
- Infinite promotion inputs are treated as unavailable. Inclusive competition
  thresholds tolerate floating-point subtraction error at exact equality.
- Drawdown includes starting equity, so first-session losses are not omitted.
- Daily and annualized equity-return volatility are separately labeled from
  cross-period return dispersion. No strategy volatility penalty was changed.

## Added verification

Final full-suite run: **394 passed, 31 subtests passed in 65.75 seconds**.
The pilot ZIP passed archive integrity checking; every CSV and configuration
JSON opened successfully, and configuration explicitly marks the run invalid.

- Cross-strategy bull/bear/flat scenarios, exact history requirements, prior-high
  exclusion, benchmark-relative scoring, volume confirmation and volatility penalties.
- Real plan builder with a manually imported temporary account, a separate-process
  restart, plan save/reload, cash preservation and missing-price reporting.
- Target cutoff, future-price perturbation, deterministic fitted predictions,
  explicit PIT audit universe, daily volatility/downside math and fail-closed coverage.
- Benchmark UI initial render and custom test-count input.
- Existing full suite includes temporary-database dashboard tests, duplicate/stale
  action checks and headless desktop launch/close/relaunch. These do not constitute
  a new manual test of the packaged Windows executable.

## Instrumented real-price pilot

Command: `.\.venv\Scripts\python.exe -m benchmarking.validate --pilot --diagnostic`

- Period: 2023-01-01 through 2023-06-01; seed 43; historical PIT universe.
- Six rule strategies, regime switching, Logistic Regression and Random Forest
  at Top 5, 10 and 20: all 13 method/period results completed without method errors.
- Corrected run: 129.0 seconds. Feature construction 58.64s; fitting 0.60s combined;
  simulations about 57s; leakage audit 12.42s. Timings include machine contention
  from concurrent tests and are indicative, not a throughput guarantee.
- All eight sampled audit/completion checks passed after the timing fixes.
- Overall result: **INVALID — incomplete historical price coverage**. Every ML
  promotion decision was INVALID. No winning strategy is reported.
- Runtime artifacts: `benchmark_exports/validation-20260925T004230815624Z/`:
  `coverage.json`, `progress.jsonl`, `pilot.json`, `pilot.zip`.
- The first diagnostic run exposed the universe-audit bug; its artifacts remain
  separate under `benchmark_exports/validation-20260925T003623684100Z/`.

## Data blocker and remaining checks

The 2023–2025 cached-price preflight found 89 affected tickers: 82 with a missing
March 23, 2023 session, plus seven missing historical series/ranges (ANSS, ATVI,
EA, FISV, SGEN, SPLK and WBA). ANSS's cache is empty; EA has only later rows.
A bounded Yahoo probe successfully returned MSFT's missing March 23 session;
it was not written over the user's existing cache. This establishes that at
least one shared gap is recoverable, not the cause of every gap.

Before a valid large study:

1. Repair gaps with backed-up, provenance-checked data and rerun full session coverage.
2. Obtain lawful, verified delisted/symbol-change histories. Do not remove those
   constituents, fabricate prices, assume permanent unavailability, or buy data
   without the user's approval.
3. Independently validate adjusted versus raw price conventions, the historical
   $5 eligibility filter, split/dividend handling, and acquisition/delisting proceeds.
   The engine's last-known-close marking does not establish correct terminal proceeds.
4. Check training-universe completeness and compare common completed periods.
5. Freeze the research configuration before inspecting a genuinely unused holdout.
   Use five months primarily, six months as sensitivity; overlapping random
   windows are descriptive stress tests, not independent confirmation.
6. Run the larger comparison only after data/integrity gates pass. A naive 100x
   extrapolation of this pilot is over 3.5 hours; later folds may have more training
   history and take longer. No large run is active in the background.

The historical universe remains reconstructed rather than licensed daily data.
Passing automated tests and sampled leakage checks is not proof of profitability
or a guarantee that every possible application state has been tested.

## Isolated price recovery (September 2026)

`python -m benchmarking.recover_data` created a checksum-verified backup and a
separate candidate cache under
`benchmark_exports/recovery-20260925T005016703146Z/`. The production `data_cache`
was not modified. Full-series, auto-adjusted refreshes recovered the shared missing
session for 82 symbols plus CRWD; FISV history was recovered under its historically
valid symbol using the Nasdaq vendor notice. The source-file hashes remained
unchanged.

Candidate coverage still fails closed for ANSS, ATVI, EA, SGEN, SPLK, and WBA.
These are valid historical constituents whose required histories are unavailable
from the configured provider. They were retained in the universe and no prices or
delisting proceeds were fabricated. Consequently no candidate-cache benchmark was
run or promoted. The read-only coverage report is under
`benchmark_exports/validation-20260926T200836660484Z/`.

Recovery now filters cached failures against required trading sessions, so an old
weekend-only failure cannot suppress a legitimate historical request. Resuming a
recovery loads candidate-only symbols and spans warmup, required observations, and
existing rows. Fresh/old adjusted-close ratios vary by more than 1% for some symbols;
because adjusted history can change with later corporate actions, the tool replaces
whole series and records ratios rather than splicing rows. Independent vendor
cross-validation remains required before adopting the candidate cache.
