# Live planning request repair — 2026-10-15

## Observed failure and diagnosis

The live Sites gateway returned HTTP 502 for `POST /api/v1/plans/current` after
125.065 seconds. Render was healthy and showed no matching completed plan request,
Python exception, or crash event. The legacy handler waits up to 150 seconds for
the market scan before saving; the gateway allows 165 seconds and the browser 180.
This architecture cannot reliably fit Cloudflare's default **125-second proxy
read timeout**, even when the origin is still working. The timing matches this
proxy limit; it does not establish whether downloads, cache locking or scoring
made that particular origin request slow.

Reference: https://developers.cloudflare.com/support/troubleshooting/http-status-codes/cloudflare-5xx-errors/error-524/

## Focused repair

- `POST /v1/plan-jobs` returns a job identifier promptly. Authenticated status
  polls return stage, ticker progress and elapsed time through short HTTP requests.
- The original strategies, sizing rules, universe selection, data loader and
  trusted version-checked plan writer are reused unchanged. The old synchronous
  endpoint remains available for compatibility; the website now uses jobs.
- One scan may run at a time per API process. Start retries retain their request
  ID; identical active requests join the running scan. Cross-user job reads return
  404, and conflicting/busy requests fail rather than starting extra scans.
- A job has a 600-second safety deadline. A timed-out Python thread cannot be
  forcibly stopped: it retains the scan slot until it drains, and its late result
  is never saved. Polling cannot save a plan or place a paper action.
- Completed job progress is kept in memory for 15 minutes, bounded to 50 records.
  A server restart loses progress, not Supabase account/history records. An unknown
  job tells the caller to refresh saved plans before retrying. This initial
  coordinator requires one API process/instance; it is not a distributed queue.
- Browser polling has a bounded 13-minute waiting window. Authentication failures
  stop immediately; bounded transient status retries poll the same job, not a new
  scan. A missing/malformed result never appears as success.
- The gateway logs only route and upstream failure status; no tokens, cookies,
  account identifiers or response bodies are included. A 524 now gives explicit
  unconfirmed-timeout guidance instead of a generic unavailable message.

## Deployment

The user authorized committing/pushing **only this focused repair** and deploying
the existing Free Render service. No service tier, payment method, secrets,
account snapshot, portfolio position, trade or competition rule is changed.
Pre-existing login polish and unrelated working-tree changes remain outside this
checkpoint commit. The independently published Sites version retains its already
public front door and application-level private-account authentication.

## Verification

Tests cover immediate response/progress, ownership, duplicate starts, busy
capacity, real consensus construction with synthetic dated prices, stale version
errors, authentication/configuration failures, timeout draining/no late save,
gateway route allowlisting and browser retry/deadline behavior. These are local
tests; the final live scan and deployment outcome must be recorded separately.

## Live result and remaining configuration blocker

- Published backend checkpoint: `cce62a8` on `main`, deployed successfully to
  the existing Free Render service (`dep-db8c5bon74is73eig6s0`).
- Published Sites version: `appgver_9053655769448191a4ed548aff370538`;
  deployment `appgdep_6ad0c321aa0c819193af239bc7d0c830` succeeded.
- One authenticated live BROAD consensus scan progressed through 226 tickers:
  KLAC 52/226 at 26 seconds, T 131/226 at 63 seconds, and plan construction at
  115 seconds. It reached the save stage instead of ending at the former proxy
  deadline. This was a current-plan scan, not a historical benchmark.
- Saving then failed with "The server's Supabase secret key has an invalid
  format." The existing stale plan was not replaced. No new plan was confirmed,
  and no paper action or portfolio update was performed.
- It is not established whether the configured value is a legacy key in the
  wrong variable or another invalid value. Browser control subsequently became
  unavailable while loading its request policy, so configuration inspection and
  another Render deployment could not be completed. No credential was copied,
  exposed, changed, or guessed.
- Follow-up source repair validates credential shape before account reads or
  expensive scans on both planning endpoints. It preserves the existing
  `sb_secret_*` API-key-only and explicitly named legacy service-role Bearer
  header paths; Supabase still validates actual credentials. It does not silently
  accept another key type or bypass the trusted writer. This follow-up needs
  a subsequent Render deployment before it affects production.
- The administrator must enter the existing Supabase secret key directly in
  Render's `STOCK_COMP_SUPABASE_SECRET_KEY`, or use the explicit
  `STOCK_COMP_SUPABASE_SERVICE_ROLE_KEY` variable for an existing legacy
  service-role JWT and remove the invalid secret-key value. Never use an anon,
  publishable, or user access token for trusted plan storage. Do not paste keys
  into chat. Once corrected, redeploy the Free service and repeat one plan scan
  and reload/history verification. End-to-end saving remains **unverified**.

The client also handles a completed start retry by fetching the owner-scoped
result: a lost initial response cannot turn a completed job into a missing-result
failure. This client correction is already in the published Sites version.

Final source verification (including the configuration preflight):

- `.venv/Scripts/python.exe -m pytest -q`: **624 passed, 41 subtests passed,
  2 existing dependency deprecation warnings**, 244.55 seconds.
- `node --test tests/*.test.mjs` from `web/`: **48 passed**.
- The same command from `deployment/chatgpt-site/`: **28 passed**.
- Hosted production build completed successfully before Sites publication.
- Browser access failure prevented a new screenshot and further live checks;
  passing local tests do not establish successful production persistence.
