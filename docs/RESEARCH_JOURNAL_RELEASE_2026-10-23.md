# Outside research and recommendation journal — 2026-10-23

## Implemented scope

- Authenticated, read-only company research route and explicit website buttons in Research and recommendation details. SEC filing metadata has dated source links; it never changes strategy scores or places trades.
- SEC requires `STOCK_COMP_SEC_USER_AGENT` containing an application name and a real contact email, configured on the Python server. Without it the response explicitly says setup is required. No contact identity is fabricated. No API key or paid service is required for SEC metadata.
- News/Massive, congressional disclosures/Quiver, and earnings-calendar ingestion are **not connected**. Missing sources are not evidence of no news. SEC metadata is not a fundamental valuation or sentiment analysis.
- History has an explicit recommendation-journal action. It reuses the latest 20 saved plans under the caller's existing database permissions and includes dismissed and recorded recommendations. This is a bounded history window, not a new immutable event database or complete action archive.
- The original saved recommendation fields remain unchanged. Reference close is the first NYSE session strictly after both signal and publication dates; 5/20/60-session returns and matched SPY excess returns use exact session endpoints. Missing prices remain null. SELL/AVOID returns describe the underlying stock, not short-sale profits.
- Calendar coverage is explicitly limited to 2025–2028. No fees, executions, sizing, dividends as separate cash flows, terminal delisting consideration, or strategy profitability are inferred.
- Journal loading is capped at 50 symbols including SPY, uses existing public-price caching, allows one background load, and returns after a 30-second budget. A cooperative deadline stops before another symbol. An in-progress provider call/cache-lock wait must finish under existing loader limits; timeout does not release its concurrency slot prematurely.
- UI rejects prior-session responses, guards double clicks, displays missing coverage, and never calls these providers merely on page rerender. Privacy copy describes the ticker/date requests.
- Deployment source allowlist includes the new API modules. Hosted tests include all gateway suites. Canonical website origin is configurable while preserving the current preview by default.
- Next.js moved from the security-flagged 16.4.0 to the compatible 16.4.3 patch line. This is a release dependency repair, not a redesign.

## Verification and publication

Final local verification: 730 pytest tests and 41 subtests passed (two existing framework deprecation warnings); 71 website tests passed; 33 gateway/build-origin tests passed in both canonical and native Sites checkouts. Production Next.js 16.4.3 build and TypeScript checks passed. `npm audit --omit=dev` reported zero known vulnerabilities. Local hosted preview returned HTTP 200 and the signed-out screen rendered. The earlier route-test failures were an outdated test fixture missing the new deadline callback; the corrected full suite passed.

Provider tests use controlled responses; they are not proof of a successful live SEC request. No owner portfolio writes, real trades, historical benchmarks, paid services, or desktop build replacement are part of this release. Backend commit `b2dd7e9` was pushed with only its nine backend/module/test/allowlist files; publication status is recorded separately after deployment completes.

## Still separate work

See `RELEASE_EXECUTION_STATUS_2026-10-23.md` for the broader backlog and evidence levels. Microsoft administrator setup, custom-domain/DNS configuration, licensed historical gaps, unattended hosted monitoring, desktop/cloud ledger synchronization, account deletion, and live AI/ML integration remain separate. This release does not mark the entire roadmap complete.
