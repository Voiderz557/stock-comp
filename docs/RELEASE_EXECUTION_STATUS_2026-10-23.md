# Release execution and dependency audit — 2026-10-23

## Scope and conclusion

Read-only source/artifact audit of `C:/Users/evann/stock-comp`; this document is the only file written by this audit. The parent owns implementation of the approved roadmap. Recommendations below are handoff work, not implementation or permission to publish. No secret values, local environment files, account databases, or account history were read. No app launch, dependency install, benchmark, market download, account mutation, deployment, commit, or push was performed.

The daily cloud planning workflow is implemented and has recorded successful live verification on October 16. Microsoft OAuth client support, local watcher, advisory AI context, desktop packaging, and isolated account tests already exist. Both Microsoft account types, the final `bizwax.org` origin, unattended scheduling, desktop/cloud account integration, CI, and complete historical research data are not established by this checkout. Source presence, an existing artifact, historical test results, and current production readiness are different evidence levels.

## Checkout and concurrent work

At inspection HEAD was `fe076b1` (2026-10-16, holding reviews alongside optional paper BUY additions). Previous relevant checkpoints are `41b78fd` (credential preflight) and `cce62a8` (bounded plan jobs). No fetch was performed; remote divergence was not assessed.

Initial dirty tree: six modified tracked files:

- `docs/live-plan-jobs-2026-10-15.md`
- `tests/test_web_auth.py`
- `web/app/globals.css`
- `web/app/page.tsx`
- `web/components/hosted-bootstrap.tsx`
- `web/tests/web-facing-audit.test.mjs`

Four untracked files were already present:

- `docs/daily-workflow-verification-2026-10-15.md`
- `docs/hosted-account-loading-fix-2026-10-12.md`
- `tests/test_daily_plan_workflow.py`
- `web/tests/hosted-daily-workflow.test.mjs`

These belong to existing work. Their changes include login/loading polish, post-action rebuild/UI coverage, and verification records. Do not overwrite or duplicate them. The old large dirty-tree description in `docs/RELEASE_READINESS.md` is an October 8 snapshot, not today's inventory. Concurrent implementation may change this inventory after inspection.

At the final status check, concurrent work had also added `api/external_research.py`, `api/recommendation_journal.py`, `tests/test_external_research.py`, `tests/test_recommendation_journal.py`, and `web/components/company-research.tsx`. This audit did not create, inspect, or validate those additions. Coordinate with their implementers; the findings below describe the inspected source, not a frozen final integration state.

## Built behavior and remaining dependencies

### Hosted daily plan and account safety

Built: authenticated cloud account snapshots, planning profiles, history/export, recommendation state, explicit estimated-close paper actions, trusted generated-plan storage, and asynchronous plan progress. Evidence: `api/public_app.py`, `api/cloud_repository.py`, `api/plan_service.py`, `api/plan_jobs.py`, `web/lib/api.ts`, `web/lib/plan-job-client.ts`, `deployment/chatgpt-site/worker/api.mjs`, and `supabase/migrations/`.

Jobs have a 600-second deadline, 900-second completed-record retention, and 50-record bound. They are process-local, with one active scan; they are not a durable queue or scheduler. A restart can lose progress. Source preserves authentication, ownership, account-version checks, and late-result/no-save handling. Keep one API process/instance until shared coordination is implemented.

`docs/live-plan-jobs-2026-10-15.md` records an October 16 successful BROAD plan in 133.9 seconds, save/reload/history, 224 quoted symbols and 223 rankable symbols, followed by successful holding-review verification after the `fe076b1` deployment. This supersedes the October 15 proxy timeout and invalid-key blocker. Do not ask the owner to replace a credential already documented as corrected. Current hosted health, configured secrets, remote migrations, and multi-user readiness were not rechecked here.

Next release verification: use disposable identities/accounts on the target origin to exercise Google and both Microsoft account types, isolation, expiry/refresh, export, account changes, stale plans, idempotent paper actions, and restart/reload. Existing RLS/lifecycle tests provide reusable fixtures; a successful single-owner smoke check does not prove all those properties in production. Requires authorized host/Supabase access and disposable accounts, not the owner's portfolio.

### Microsoft personal and work/school accounts

Built: `web/lib/supabase.ts` checks the public Azure provider flag and calls `signInWithOAuth` with provider `azure` and `email` scope. The current dirty `web/app/page.tsx` shows the Microsoft button only when available; `tests/test_web_auth.py` tracks that behavior. The callback page exists. `web/README.md` already documents external registration. The October 8 provider-disabled observation is historical; current remote provider state is unknown.

Administrator setup still requiring verification:

1. Use an Entra registration supporting **Accounts in any organizational directory and personal Microsoft accounts**.
2. Set its Web redirect to `https://<project-ref>.supabase.co/auth/v1/callback`.
3. Enter client ID and client secret directly in Supabase's Azure provider settings; enable it. Use the `common` tenant rather than a personal-only or organization-only restriction.
4. Allow the final site's `/auth/callback` in Supabase and test one personal and one work/school identity, including logout/reload and separate account ownership.

Requires owner/admin access to Entra and Supabase; organizational consent policies may require that tenant's administrator. No premium license requirement was established for this OAuth path; it is not a request to enable enterprise SAML. Never paste secrets into chat or browser variables. Do not introduce application-level account merging based only on matching email.

Setup reference checked in this audit: [Supabase Azure sign-in](https://supabase.com/docs/guides/auth/social-sign-in/auth-azure). Provider enablement alone does not prove both account types work.

### bizwax.org, HTTPS, and deployment configuration

Built: static Next frontend, Sites gateway packaging, Python Docker context allowlist, Render Blueprint, canonical URL configuration, and public environment template. Evidence: `web/.env.example`, `web/app/layout.tsx`, `web/app/robots.ts`, `web/app/sitemap.ts`, `supabase/config.toml`, `deployment/chatgpt-site/scripts/build-hosted.mjs`, `deployment/python-api/`.

Concrete source gap: the hosted build script explicitly sets `NEXT_PUBLIC_SITE_URL` to the preview `chatgpt.site` origin. Changing only `web/.env.example` cannot configure the hosted canonical origin. `supabase/config.toml` lists localhost/preview callbacks, not `bizwax.org`; it is not evidence of remote auth settings. `deployment/python-api/README.md` still says no Render account/deployment exists, contradicted by the later release record.

Next external setup:

1. Owner supplies access to the registrar/DNS zone and selected frontend hosting account; determine canonical apex versus `www` and the alternate-host redirect.
2. Confirm that host supports the chosen custom domain and gateway runtime on the selected plan. Obtain its exact verification and routing records before editing DNS; preserve unrelated mail/TXT records. Issue/verify TLS.
3. Parent makes hosted site origin configurable, then the deployer sets the exact HTTPS origin, disables demo mode, provides the public privacy contact, and points the gateway at the existing HTTPS API.
4. Set Supabase Site URL and exact callback allowlist, Google Authorized JavaScript origins where direct GIS is used, and API `STOCK_COMP_ALLOWED_ORIGINS`. Keep trusted storage keys only on the Python server. Verify HTTPS redirects, login, reload, and API behavior on the final origin.

No DNS lookup, domain ownership check, host entitlement check, or DNS mutation was performed. Do not invent a CNAME/IP or treat domain connection as already authorized in this audit. Domain registration/renewal and any host upgrade need the owner's account/cost decision. Frontend custom-domain hosting is separate from branded Supabase authentication. Supabase custom auth domains are an optional paid add-on on a paid plan; retaining the default auth hostname avoids that specific add-on. [Current Supabase custom-domain documentation](https://supabase.com/docs/guides/platform/custom-domains).

### Historical and current-market data

Built: point-in-time universe, coverage diagnostics, isolated recovery, attributable CSV/Parquet importer, and fail-closed checks for missing histories/unmodeled terminal events. Evidence: `data/historical_universe.py`, `data/coverage_diagnosis.py`, `benchmarking/import_prices.py`, `benchmarking/recover_data.py`, `benchmarking/simulation.py`, `docs/HISTORICAL_DATA_GAPS.md`, `docs/VALIDATION_STATUS.md`.

Latest documented historical blockers remain ANSS, ATVI, EA, SGEN, SPLK, and WBA. The isolated recovery and validation directories named in those docs exist. This audit did not inspect their data or repeat coverage. The older shared-session/FISV problems were subsequently recovered in the candidate cache; do not report all original gaps as unresolved. Candidate recovery is not production-cache adoption, and recorded source hashes later diverged for AAPL/coverage metadata.

Required input is attributable daily OHLCV for the six names over the documented required spans (starting 2020-04-29; relevant last-public sessions, or through 2025-12-31 for EA's competition evaluation), adjustment conventions consistent with SPY/QQQ, corporate-action evidence, and explicit terminal consideration handling. Parent can improve provenance/import guidance and terminal-event validation without obtaining data or silently changing strategy formulas. Complete data and an independent convention check must precede candidate adoption or any profitability leaderboard.

User dependency: lawful exports or access to a suitable data vendor. Vendor subscriptions/tokens and redistribution rights depend on the selected product; no paid vendor was activated or entitlement verified. Existing vendor suggestions in the gap report are candidates, not confirmation that a particular plan supplies every required history. Public website redistribution/license review also remains open. Current discovery coverage does not cure historical gaps; the October 16 live SPCX limitation was insufficient long-lookback history, not a missing current quote.

### Watcher and scheduling

Built: `paper_trading/market_watcher.py` and the `app/competition_dashboard.py` Streamlit fragment provide manual checks and opt-in 15-minute checks while the page remains open. They watch holdings and saved BUY candidates, daily-close moves/volume, signal/plan changes, staleness, missing quotes, and capacity, with deduplication and separate watcher storage. See `docs/MARKET_WATCHER.md` and `tests/test_market_watcher.py`.

Not established: unattended operation with the UI closed, a Windows scheduled task, hosted recurring worker, push/email alerts, or intraday monitoring. No scheduler registration was performed, and OS scheduled-task state was not inspected. Async planning jobs are user-requested compute, not scheduled market monitoring.

Safe parent work: reusable read-only check entry point, explicit schedule settings, bounded retry/deduplication, and tests with synthetic clocks/prices. Activation needs the chosen runtime, timezone/market-calendar policy, explicit opt-in, and notification destination if desired. Local scheduling requires the machine/session availability appropriate to the chosen mechanism; hosted scheduling requires a reliable runtime and a quota/cost review. Email/push would additionally require provider credentials and consent. No existing paid scheduler requirement was established.

### AI context and provider access

Built: `paper_trading/analysis_context.py` builds current account/holdings, saved/prior plan, freshness, warnings, capacity, and bounded recent closed-trade context. `ai/ui.py` previews data and calls the provider only on Analyze; `ai/analyze.py` distinguishes facts/signals/interpretation and provides no mutation tools. `ai/settings.py` is environment-based; `ai/providers/null.py` and `ai/providers/openai_compatible.py` support disabled and compatible HTTP modes. Tests exist in `tests/test_analysis_context.py` and `tests/test_ai_analyze.py`.

Limitations: this is local SQLite context, not a verified cloud AI workflow or complete action history. No cloud AI/watcher integration was found in the inspected API/client routes. The model's instruction adherence is not guaranteed by tests. Ordinary rerenders must not invoke a provider.

Next external input, only if live AI is desired: selected compatible endpoint/model and owner-managed API credential, entered outside source through `STOCK_COMP_AI_ENABLED`, `STOCK_COMP_AI_PROVIDER`, `STOCK_COMP_AI_MODEL`, `STOCK_COMP_AI_BASE_URL`, and `STOCK_COMP_AI_API_KEY` as applicable. A hosted API may incur usage charges; no subscription entitlement or free quota was assumed. Core plans and the null provider require no AI account. Parent can extend redacted cloud context and fake-provider tests without calling an external model.

### Windows desktop and web connection

All three expected executables exist, with filesystem modification dates September 23, 2026:

- `dist/StockCompDashboard/StockCompDashboard.exe`: 31,707,785 bytes.
- `dist/StockCompDashboardAI/StockCompDashboard.exe`: 31,729,570 bytes.
- `dist/StockCompDashboardManual/StockCompDashboard.exe`: 31,813,442 bytes.

Existence does not prove these binaries contain current source or pass a current smoke test. None was launched, rebuilt, copied, or replaced.

Available scripts: `Launch-Paper-Trading.bat` and `launch_competition_dashboard.bat` run Streamlit; `build_desktop_app.bat`, `build_desktop_app_ai.bat`, and `build_desktop_app_manual.bat` install packaging dependencies and invoke PyInstaller with their matching specs. Therefore build scripts are not read-only checks. AI/manual builds target separate folders from the baseline but can overwrite their own previous outputs; the baseline script targets the known-working baseline. Future releases should use a new versioned staging directory and temporary-data smoke checks.

`desktop/launcher.py` starts localhost Streamlit in pywebview and initializes storage/logs on launch. `desktop/paths.py` stores packaged runtime data under `%LOCALAPPDATA%/StockComp`, copies legacy data with backup when needed, and keeps an existing destination ledger. These are reasons not to launch it during this audit. Tests exist in `tests/test_desktop_paths.py` and `tests/test_desktop_launcher.py`.

Desktop SQLite and Supabase cloud records remain separate ledgers. A web link alone is not synchronization. Parent can add an explicitly labeled hosted-site entry point and version/connection status without merging accounts. A hosted desktop shell or migration/sync implementation needs an explicit account-source/conflict policy and an export/rollback design; no cloud secret belongs in a desktop package. No paid runtime requirement was demonstrated by this inspection; clean-Windows runtime availability and redistribution requirements remain release checks.

### CI and dependency reproducibility

No `.github/workflows` directory exists in the inspected checkout. This establishes missing checked-in GitHub workflow configuration, not absence of every possible external CI service. Tests and build commands exist, but CI execution/results were not queried.

`requirements.txt` and `requirements-desktop.txt` are unpinned; API/container requirements use bounded ranges, not a full transitive lock. `web/package-lock.json` exists. Dependency metadata does not establish vulnerability status or reproducibility across platforms. No dependency advisory scan or network package resolution was performed.

Concrete command gap: `deployment/chatgpt-site/package.json` defines `npm test` as only `tests/hosted-api.test.mjs`, omitting `tests/plan-jobs.test.mjs`. CI should use both suites explicitly or the full test glob. `web/package.json` has build/dev/start commands but no test script; use the existing Node test command rather than assuming `npm test` works there.

Safe parent work: add bounded CI for isolated Python tests, web and gateway tests, TypeScript/build checks, disposable migration tests, secret scanning, and dependency advisories; use synthetic prices and no live secrets/accounts. Add tested dependency locks and versioned release manifests in separate reviewed work. Do not include long historical benchmarks or deploy jobs by default. Enabling CI remotely requires repository admin/settings access and review of runner-minute allowances; a paid runner plan is not established as necessary.

## Checks and evidence boundaries

Fresh read-only checks in this audit:

- Git status, recent local log, diff statistics and selected diffs; source/docs/template inspection. No secret files or environment values inspected.
- Existence/metadata of the three executables; `.venv/Scripts/python.exe`, `web/node_modules`, `web/package-lock.json`, and `web/out/index.html` exist.
- `deployment/chatgpt-site/out/index.html` and `deployment/chatgpt-site/dist` do not exist in this checkout. The separate published Sites source/artifact was not inspected, so this does not invalidate its recorded deployment.
- PATH resolves Node, npm and Python. Docker and Supabase commands were not found on PATH; this does not prove they are unavailable via every local installation or package runner.
- Node `v24.18.0`; virtualenv Python `3.11.9`. Read-only installed metadata reports pytest `9.1.1`, PyInstaller `6.22.3`, pywebview `6.2.1`, Streamlit `1.63.0`, FastAPI `0.141.1`, yfinance `1.7.0`.
- Python `-B` AST parsing passed for seven files: desktop launcher/paths, API auth/jobs, watcher, analysis context, and AI analyzer. Modules were not executed.
- `node --check` passed for `deployment/chatgpt-site/worker/api.mjs` and `deployment/chatgpt-site/scripts/build-hosted.mjs`.

Historical checks, consolidated without claiming a fresh rerun:

- October 8 release readiness: 558 Python tests, 41 subtests, 10 web tests, local build/health observations; remote migration check incomplete at that time.
- October 15 workflow verification: 162 focused checks with disposable inputs/storage; its initial live planning failure was subsequently repaired.
- Latest October 16 record in `docs/live-plan-jobs-2026-10-15.md`: 628 Python tests plus 41 subtests, 56 web tests, 28 deployment gateway tests, 25 native Site gateway tests, production TypeScript/build checks, and live plan/holding-review reload verification. These counts belong to that recorded state, including its independent Site checkout.

No fresh full suite, browser OAuth journey, dependency security audit, production health check, database migration check, clean desktop installation, or full historical coverage audit was performed. Do not attach old test counts to the parent's future changes without rerunning appropriate checks.

## Immediate handoff order

1. Parent can concurrently prepare CI/test command coverage, configurable hosted origin, release-doc corrections, isolated watcher/AI work, and versioned desktop packaging. Reuse existing modules/tests and coordinate overlap with the dirty UI changes.
2. Owner/admin supplies Entra/Supabase setup access, both Microsoft test identities, DNS/hosting access, and canonical-domain choice. No secrets should be delivered through this document or chat.
3. Obtain licensed historical exports and source/adjustment evidence; keep profitability claims blocked until complete coverage and terminal handling pass. This can proceed independently of login/domain work.
4. Integrate parent changes, run bounded isolated checks plus the required release build/smoke checks, then record exact artifact/commit versions. Deployment and live verification remain separate authorized operations; this audit performs neither.

The public rulebook, legal/privacy ownership, provider data rights, clean restore/rollback, and production multi-user isolation remain release dependencies in `docs/PROJECT_MASTER_ROADMAP.md`. An approved implementation roadmap does not itself supply domain credentials, vendor licenses, or evidence that these gates have passed.
