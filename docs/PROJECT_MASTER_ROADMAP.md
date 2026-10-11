# Stock Competition project master roadmap

Status: working plan as of 2026-10-08. This is a paper-trading and research project, not an autonomous brokerage agent. **A good backtest or recommendation does not guarantee profit.** The ordering below deliberately puts data integrity, account safety, and usable paper workflows ahead of adding more indicators.

## What exists today

The repository has a Python daily-plan engine, several registered rule strategies, a rule-based SPY/QQQ regime selector, a local SQLite paper account, a Windows desktop wrapper, a Next.js website, a FastAPI service, Supabase-backed cloud account records, historical backtests, and an ML research/benchmarking stack. The website can save account snapshots, build dated plans, review suggestions, and explicitly record paper-only actions when the required cloud migrations are installed. The desktop account and the cloud account are **separate ledgers**; do not imply they sync.

The current scanner has three distinct concepts:

- The Nasdaq-heavy seed list has 101 symbols.
- The default expanded current-day watchlist has 139 symbols after excluding verified ended listings EA and WBD.
- The optional broad cross-sector watchlist has 224 symbols and is selected in website Settings. It is a **discovery list**, not a certified list of competition-eligible stocks and not a historical index.

Historical benchmarks continue to use `data/historical_universe.py` point-in-time membership. Adding current watchlist symbols must never backfill them into old dates. The data loader checks dated quotes and reports unavailable names. The default remains the 139-symbol list because a broad scan can take longer and provider coverage has not been established for every added ticker. The current-listing audit is a separate check against the Nasdaq Trader directories; it does not certify Yahoo price-history coverage.

The website's VIX card is a separate dated reading. VIX is an options-implied measure of expected 30-day S&P 500 volatility; the regime classifier still uses **realized** SPY/QQQ return volatility. They are not interchangeable. [Cboe VIX overview](https://cdn.cboe.com/resources/education/research_publications/vixfactsheet2019.pdf).

The current research evidence does **not** justify claiming that any strategy beats SPY reliably. [VALIDATION_STATUS.md](VALIDATION_STATUS.md) records invalid historical coverage and corrected leakage issues. [STRATEGY_RESEARCH_ROADMAP.md](STRATEGY_RESEARCH_ROADMAP.md) records preregistered strategy trials, including failed variants. [HISTORICAL_DATA_GAPS.md](HISTORICAL_DATA_GAPS.md) records unresolved acquired/delisted histories. An exploratory blacklist cannot be relabeled a complete benchmark.

## The product we are trying to build

One understandable assistant should answer five questions before a human makes a paper-trading decision:

1. What account do I have now, including cash, holdings, long/short exposure, and remaining capacity?
2. What changed since the last dated plan and market session?
3. Which candidates qualify, which do not, and why did the algorithms disagree?
4. What size is suggested, how much cash stays unused, and what warnings or exit levels matter?
5. How strong is the evidence behind this recommendation, and what would invalidate it?

The website is the primary public product. The Windows app remains a useful local companion and must retain its existing data and executable builds. TradingView is an optional **external paper broker/charting workflow**, not a second internal trading engine. Real brokerage execution is outside the present scope.

## Non-negotiable rules

- Do not silently change strategy formulas, benchmark assumptions, historical constituents, competition caps, or user holdings.
- Never turn a missing quote into zero, an entry-price estimate, a BUY, or a claim of current portfolio value.
- Display market-data dates, plan-generation time, account-snapshot time, and action time separately.
- A recommendation is not an order. A paper action requires an explicit review and confirmation. No real trades are placed.
- Preserve SQLite files, account history, saved plans, generated exports, secrets, and known-working desktop builds during development and upgrades.
- Reject invalid data and leakage rather than filling gaps with today's constituent list or stale prices.
- Do not purchase data, enable paid infrastructure, connect trade-capable brokers, deploy publicly, commit, or push as a side effect of an unrelated task.
- For a public launch, review legal text, privacy controls, provider licenses, and competition rules with their actual owners. Draft legal pages are not legal clearance.

## Priority 0 — make the current product trustworthy

### 0.1 Verify the actual competition rulebook

- [ ] Record the official contest start/end dates, eligible exchanges/instruments, minimum price, shorting permission, margin treatment, max position definition, and whether appreciation over the cap requires trimming.
- [ ] Keep the app's $5 and $20,000 constants labeled as **configured assumptions** until compared with the current rulebook. Do not let a research-only sub-$5 filter bypass the paper BUY guard.
- [ ] Add a dated, versioned rule checklist with a link to the rulebook and the date it was checked.
- **Done when:** a test and a human-readable rule matrix agree with the published rules; unsupported settings are clearly research-only.

### 0.2 Protect account and deployment data

- [ ] Confirm all portfolio databases, caches, secrets, downloaded datasets, and `dist/` builds are outside deployment artifacts and ignored by Git where appropriate.
- [ ] Exercise backup/restore against temporary copies, including migrations between packaged app versions. Never run a migration against the user's sole copy.
- [ ] Ensure cloud row-level security isolates users, storage RPCs check ownership, and idempotency prevents repeated paper fills.
- [ ] Add a release checklist: version, backup, migration, rollback path, smoke test, and data export.
- **Done when:** temporary-user tests demonstrate isolation and a failed upgrade can restore the prior ledger and executable.

### 0.3 Close the biggest historical-data gap

- [ ] Keep ANSS, ATVI, EA, SGEN, SPLK, and WBA in historical membership where they belong; never drop them merely to finish a benchmark.
- [ ] Obtain attributable OHLCV through each relevant last-public session, plus documented corporate actions and terminal proceeds. Use the existing source-neutral importer and separate candidate cache.
- [ ] Cross-check raw versus adjusted conventions against SPY/QQQ and at least one independent source before production-cache adoption.
- [ ] Require complete session coverage for every strategy, ML fold, and common evaluation period. Treat absent data as invalid, not a losing or winning return.
- **Done when:** the coverage audit passes without a historical blacklist, with a provenance record and a recoverable cache backup. See [HISTORICAL_DATA_GAPS.md](HISTORICAL_DATA_GAPS.md).

## Priority 1 — finish the daily website workflow

### 1.1 One account, one clear daily plan

- [ ] Make the first-run path obvious: sign in, enter starting capital/cash/holdings, review, save, then build a plan.
- [ ] Show whether a plan was generated for the current portfolio version and most recent available market session. On any account mutation, make the old plan visibly stale.
- [ ] Present recommended whole shares and dollar size, reasons, strategy votes, conflicting signals, quote date, capacity, and cash left over. Do not force exactly five holdings or fill unused cash.
- [ ] Make `BUY`, `SELL`, `WAIT`, and `AVOID` visually distinct; separate a suggestion to review an exit from an order to sell now.
- [ ] Keep user-selected stop/target guidance opt-in and explicitly advisory. TradingView Paper Trading supports attached stops/targets, but linked-exit behavior and fill prices must be reviewed by the user. [TradingView paper-trading features](https://www.tradingview.com/support/solutions/43000516466-paper-trading-main-functionality/), [linked exit behavior](https://www.tradingview.com/support/solutions/43000772334-multiple-take-profit-and-stop-loss-levels/).
- **Done when:** a new user can complete the workflow, close/reopen the browser, and correctly explain which values are current, estimated, or unavailable.

### 1.2 Clear scanner coverage and speed

- [x] Show the chosen 139- or 224-symbol mode and scan counts in each saved plan summary, including history, not just in Settings.
- [x] Show scanned, quoted, stale, failed, and rankable counts; include missing symbols and reasons in a collapsible plan-audit panel. Rankable does not imply BUY or competition eligibility.
- [x] Measure cached and cold broad scans with stage timings before raising timeouts or making broad mode the default. The initial sequential 224-symbol cold scan took 300.9s (293.5s price history); bounded four-worker loading reduced a separate cold run to 57.3s (52.8s price history) and its warm run to 4.6s. Both cold runs quoted 224/224 and ranked 223/224; SPCX lacked enough long-lookback history. These are single-machine observations, not a guaranteed latency or provider service level.
- [ ] Reuse the market-data cache and failure-range cache; do not retry known impossible listing ranges. A generic provider error is not proof a stock is permanently unavailable.
- [x] Audit every broad-list symbol against current exchange directories and map BRK-B to exchange notation BRK.B. Current listings do **not** establish past index membership or provider-history completeness. [Nasdaq symbol directory definitions](https://www.nasdaqtrader.com/trader.aspx?id=symboldirdefs).
- [ ] Consider deterministic batches or background jobs if broad scans regularly exceed the website's request timeout. Preserve snapshot/version checks when work finishes.
- **Done when:** broad mode completes within a documented budget on representative machines and incomplete coverage is never hidden.

The [official Nasdaq Trader directory](https://www.nasdaqtrader.com/trader.aspx?id=symboldirdefs) audit on 2026-10-08 matched all 224 current broad symbols after mapping Yahoo's `BRK-B` to exchange notation `BRK.B`. EA and WBD were excluded only from current discovery following their confirmed trading ends; historical constituent records remain untouched. [Marsh changed MMC to MRSH](https://www.marsh.com/en/corp/about/news/marsh-mclennan-to-change-nyse-symbol-to-mrsh.html). During the scan, SPCX had a dated quote but no usable long-lookback strategy output because [it only began trading on 2026-06-12](https://nasdaqtrader.com/TraderNews.aspx?id=DTN2026-8); its verified listing boundary now prevents impossible pre-IPO provider requests. The measured scan was read-only with a temporary cache and synthetic cash account, not a profitability benchmark or a saved plan.

Live plan price loading now uses four bounded workers and per-process cache serialization; historical callers stay sequential by default. Live provider failures are isolated from the historical failure cache and retried after 15 minutes, so a temporary Yahoo error is not a permanent live blacklist. A 32-symbol cold probe fell from 42.5s sequential to 8.9s parallel, with 32/32 quotes and no provider failures. Some independently fetched adjusted-price frames differed at floating-point precision; a follow-up 8-symbol sample had no material frame differences at $0.0001 price tolerance, despite individual closing-price differences up to $0.000061. Exact equality of separately fetched Yahoo floats is not asserted. The website API now returns a clear no-save timeout after 150s of building, ahead of the browser's 180s request limit. A future production multi-worker deployment still needs a shared cache/job coordination design; this process-local lock is not cross-process locking.

### 1.3 Account and plan history

- [ ] Preserve imported holdings as dated account snapshots, not fictitious fills. Keep exact cost basis when supplied.
- [ ] Show previous-versus-current plan changes and explain dropped names only from saved evidence; otherwise state that the cause is unknown.
- [ ] Show recent paper actions as recent, not as the complete action history. Keep full export available.
- [ ] Add a human-readable activity filter: plan generated, account snapshot replaced, recommendation dismissed/restored, paper action recorded, and plan invalidated.
- [ ] Keep export private, support clear retention/deletion requests before public launch, and document what an export can and cannot restore.
- **Done when:** actions can be traced to a plan/account version without inventing missing history.

### 1.4 Public login and deployment

- [ ] Verify Google login/logout and a second browser/device against the actual hosted origin, not only `127.0.0.1`.
- [ ] Finish Microsoft Entra configuration for **both personal and work/school** accounts; test one of each. Do not automatically merge identities just because email addresses match.
- [ ] Select a free-compatible hosting plan for `bizwax.org`, configure HTTPS/DNS, production CORS, Supabase redirect URLs, and Google Authorized JavaScript origins. Never put server secrets in `NEXT_PUBLIC_*`.
- [ ] Accept that a free Supabase OAuth redirect may show the project's `supabase.co` hostname. Supabase documents branded custom domains as a paid add-on; do not enable it without the user's approval. [Supabase custom domains](https://supabase.com/docs/guides/platform/custom-domains).
- [ ] Run deployment-specific RLS, auth expiry, rate-limit, session refresh, passwordless email quota, error-page, and backup tests. The current Supabase Free plan has limits that should be checked before a public launch. [Supabase billing and Free plan](https://supabase.com/docs/guides/platform/billing-on-supabase), [auth rate limits](https://supabase.com/docs/guides/auth/rate-limits).
- **Done when:** private beta users can sign in and access only their own data on an HTTPS website, with no paid feature silently enabled.

## Priority 2 — improve research evidence before changing the bot

### 2.1 A defensible common-period benchmark

- [ ] Freeze strategy versions, model settings, universe reconstruction, price source, evaluation dates, and the research-trial ledger **before** looking at holdout outcomes.
- [ ] Use common five-month periods as the primary competition test and six months as sensitivity; separately report overlapping-window dependence.
- [ ] Compare every method on the exact same members, sessions, prices, costs, cash treatment, and rebalance dates. Fail closed for missing methods or constituent histories.
- [ ] Add transaction-fee and slippage sensitivity, but do not retrofit them into old results without versioning.
- [ ] Report median excess return, beat-SPY rate, worst period, drawdown, turnover, cash occupancy, and `P(Return >= 10/15/20/25/30%)` with completed-test denominators. Exact equality counts.
- [ ] Separate exploratory tuning, validation, untouched holdout, and forward shadow results. Never describe a blacklist or reused holdout as promotion evidence.
- **Done when:** a ZIP can be independently audited and a second run with the same seed/config reproduces results.

### 2.2 Determine whether the strategies are genuinely different

- [ ] Attribute each period's result to regime path, selected names, rejected names, position contributions, cash, and exposure.
- [ ] Measure overlap and return correlation among Baseline, Momentum V2, Aggressive Momentum V1, Relative Strength Momentum V1, Breakout Volume V1, Mean Reversion V1, and research-only Controlled Breakout V1.
- [ ] Do not promote an extra strategy because it has a new label or slightly better average. Demand incremental performance or a useful different failure mode.
- [ ] Compare a simple benchmark such as SPY buy-and-hold and a cash-heavy baseline under identical accounting.
- **Done when:** each proposed strategy has a preregistered rationale, documented failure modes, and evidence from complete, untouched periods.

### 2.3 Test breadth and VIX as information, not decoration

- [ ] Keep current VIX display-only until dated VIX history, freshness rules, and a preregistered hypothesis are available. Never substitute implied VIX for daily-return standard deviation inside existing strategies.
- [ ] Evaluate point-in-time breadth from historical constituents with explicit eligible-count and missing-data denominators.
- [ ] Ask whether breadth or VIX adds predictive value **after** existing trend/volatility features, not merely whether a chart looks plausible.
- [ ] Compare directional and high-volatility markets separately, with false-alarm counts and missed-opportunity costs.
- **Done when:** a new regime signal improves untouched risk/return or alert quality without relying on one crash or one rebound.

### 2.4 Assess ML honestly

- [ ] Keep Logistic Regression and Random Forest as rankers, not execution engines. Maintain train-only imputation/encoding/scaling and strict label-maturity purging.
- [ ] Require the automated leakage audit, feature stability report, class balance, probability calibration, and Top-N results on common complete periods.
- [ ] Compare against both rule-based strategies and a deterministic random ranking; account for multiple tested model ideas.
- [ ] Leave the ML promotion gate `INVALID` when coverage, fold count, or leakage checks fail. A model's classification accuracy is not a profit claim.
- **Done when:** repeated untouched folds show stable, economically relevant improvement after realistic costs; only then discuss shadow integration.

## Priority 3 — make the assistant proactive but safe

### 3.1 Read-only market watcher

- [ ] Extend the existing local watcher gradually: stale plan, missing quote, unusual position move, signal change, exposure/capacity threshold, and unusual volume.
- [ ] Deduplicate alerts, rate-limit them, show the exact data timestamp, and allow the user to mute a class of alerts.
- [ ] Start with in-app alerts; add email or desktop notifications only after consent, cost, and reliability checks.
- [ ] Never infer an intraday trigger from end-of-day data. Market hours, delayed quotes, and provider outages must be visible.
- **Done when:** scripted time/price changes produce one correct alert and no paper action or broker call. See [MARKET_WATCHER.md](MARKET_WATCHER.md).

### 3.2 Optional AI explanation layer

- [ ] Reuse the existing structured, read-only desktop context and inspect exactly what would be sent to an AI provider. Keep keys/paths/account identifiers out of prompts and logs.
- [ ] Separate recorded account facts, deterministic algorithm signals, and AI interpretation in every answer. Treat ticker notes and reasons as untrusted text.
- [ ] Offer a model/provider choice without assuming paid API access. No provider call on ordinary rerenders; user click or explicit opt-in only.
- [ ] Test missing configuration, timeout, rate limit, bad response, prompt injection, stale plan, and provider outage. AI must have no mutation tools.
- [ ] Only after read-only behavior is reliable should an AI explain watcher alerts; it still must not select fills or execute trades.
- **Done when:** the provider can be disabled without impairing the core daily assistant, and fake-provider tests leave the database unchanged.

### 3.3 External research and account connectors

- [ ] Evaluate Quiver on specific questions first: provenance, publication delay, historical availability, license, rate limits, price, and incremental out-of-sample value. Do not treat delayed disclosures as real-time signals.
- [ ] Treat Liquid or another trade-capable connector as a later, separate security decision. Prefer a read-only connection if offered; never grant trade scopes just to obtain market context.
- [ ] Keep broker data separate from self-reported competition and TradingView paper accounts. Reconciliation needs explicit account identity, cash, holdings, and timestamp.
- **Done when:** a connector has a narrowly scoped data contract, a threat review, a cost decision, and a deterministic disable path.

## Priority 4 — polish and ship, in that order

### 4.1 Website usability and accessibility

- [ ] Make plan status, quote staleness, account capacity, and disabled-action reasons obvious without opening several dialogs.
- [ ] Use short, readable candidate cards/tables on narrow screens; show detail on demand instead of walls of strategy names.
- [ ] Add keyboard, screen-reader, contrast, error-focus, and mobile viewport checks. Test empty account, missing prices, many holdings, and long explanations.
- [ ] Distinguish current watchlist prices from plan-generation prices, and show an explicit loading/progress state for long scans.
- **Done when:** a new user can finish setup and explain a recommendation on both desktop and phone without guidance.

### 4.2 Desktop release path

- [ ] Keep the known-working `.exe` intact. Package new versions into a different output folder and test launch, close, relaunch, and persistent data with a temporary database.
- [ ] Confirm localhost-only binding, child-process cleanup, error logging without secrets, and migration backup/restore.
- [ ] Decide whether desktop remains a local-only account or eventually becomes a shell around the hosted web account. Do not silently merge the two ledgers.
- **Done when:** a clean Windows machine can run the package without separately installed Python and existing local data survives an upgrade.

### 4.3 Production operations and privacy

- [ ] Complete privacy/cookie disclosures to match actual analytics, auth storage, retention, third-party processors, and any optional AI/market-data service. Revisit them when telemetry or alerts are added.
- [ ] Define an incident playbook: leaked credential, lost database, bad migration, wrong price, account mix-up, and provider outage.
- [ ] Add CI checks for Python tests, web build/tests, migration tests, secret scanning, and dependency advisories. Do not run expensive historical benchmarks on every commit.
- [ ] Monitor free-tier quotas and data-provider terms; never assume a no-cost prototype can serve unlimited public users.
- **Done when:** a failed release can be rolled back and users can be told precisely which data and actions were affected.

## Next five bounded work packages

1. **Broad-scan proof:** validate current symbols/provider mappings, instrument one broad scan, and add coverage/progress counts. Keep the default 139 until performance is measured.
2. **Paper-account end-to-end test:** temporary cloud/local account, snapshot → plan → review → simulated action → stale plan → rebuild → export. No real user ledger changes.
3. **Historical coverage decision:** identify a lawful source for the six unresolved histories or explicitly pause promotion-grade benchmarking; do not blacklist them.
4. **Private website beta:** configure `bizwax.org`, test Google and Microsoft accounts, RLS isolation, privacy pages, and free-tier limits before public access.
5. **Evidence review:** one frozen, complete-coverage walk-forward comparison, with costs and attribution, before considering new indicators, VIX rules, ML promotion, or live recommendations.

## Decision rules for new ideas

Before adding a strategy, indicator, AI agent, external feed, broker connector, or automation, write down: the problem it addresses; the exact information available at decision time; a simple comparator; expected benefit; data cost; failure mode; how it will be tested; and a stopping condition. Record failed trials too. Prefer deleting an idea that adds complexity without measurable value over adding it to a giant consensus score.

## Explicitly deferred

Automatic real trading, copying a TikTok prompt into a trade engine, paid data/API fallbacks without approval, universal brokerage profiles, full historical backfills from today's watchlist, unrestricted AI tools, and claims of guaranteed 20%+ five-month returns are **not** part of the next release.

## Related project references

- [DEVELOPMENT_PLAN.md](DEVELOPMENT_PLAN.md) — earlier long-term strategy/product vision; some aspirations there predate later safety fixes.
- [STRATEGY_RESEARCH_ROADMAP.md](STRATEGY_RESEARCH_ROADMAP.md) — documented strategy trials and promotion criteria.
- [VALIDATION_STATUS.md](VALIDATION_STATUS.md) — verification and invalid-coverage checkpoint.
- [ML_RESEARCH_PLAN.md](ML_RESEARCH_PLAN.md) — feature timing, labels, walk-forward, and ML limitations.
- [HISTORICAL_DATA_GAPS.md](HISTORICAL_DATA_GAPS.md) — missing delisted histories and acquisition handling.
- [PUBLIC_APP_ARCHITECTURE.md](PUBLIC_APP_ARCHITECTURE.md) — cloud identity, isolation, and data architecture.
- [MARKET_WATCHER.md](MARKET_WATCHER.md) — existing local read-only watcher.
- [web/README.md](../web/README.md) — website setup and current workflow.
