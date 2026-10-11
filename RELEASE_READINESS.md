# Public website release readiness

Status on 2026-10-08: **local prototype verified; public release not verified**. The
Windows desktop build and local paper-trading data are separate and untouched.
No deployment, migration, real trade, commit, or push was performed in this pass.

## Verified locally

- `web/` production build completed (`npm run build`). The localhost page rendered
  the signed-in overview in a read-only browser check. This does not establish
  that every button or a hosted OAuth redirect works.
- `web/tests/account-lifecycle-sql.test.mjs` exercises all current SQL
  migrations in a **disposable in-memory Postgres** database as an authenticated
  RLS user. It covers account creation, imported holdings, duplicate/stale
  submission rejection, plan creation, dismissal/reopening, estimated paper
  action with idempotency, stale-plan blocking, rebuilding, history, and account
  reimport. A second user's account is present to test visibility boundaries.
- Web tests: 10 passed (`cd web; node --test tests/*.test.mjs`). Python tests:
  558 passed, 41 subtests passed, 2 deprecation warnings
  (`.venv\Scripts\python.exe -m pytest -q`).
- The local API, website, and Streamlit health endpoints responded. This is a
  process check, not a cloud reliability or data-quality check.
- The Supabase CLI can list the linked project through the management API.
  Remote migration status was **not** verified: the read-only migration-list
  command did not finish within 50 seconds and was stopped. The Docker CLI is
  not available on PATH, so the local Supabase stack/RLS SQL script could not
  be run here.
- The linked project's public authentication settings currently report Google
  enabled and Microsoft/Azure disabled. The local web configuration has no
  direct Google Identity Services client ID, so it uses the redirect fallback.
  Provider flags alone do not prove a successful OAuth callback.

## Release gates still open

1. Confirm every migration in `supabase/migrations/` is applied to an isolated
   development project. Run `supabase/tests/public_app_rls.sql` there and verify
   the live account/export paths with two disposable users. Do not test by
   changing the owner's actual paper account.
2. Complete browser sign-in/sign-out and account-flow checks with disposable
   Google and Microsoft identities. Microsoft requires an external Entra app
   registration; Google and Microsoft provider availability is not proven by
   a localhost render. Test redirects on the final HTTPS origin.
3. Select/configure hosting, DNS, TLS, API CORS, production environment, and
   Supabase redirect allow-list for `bizwax.org`. Ensure production demo mode
   is off and no service-role/API secret enters browser variables.
4. Test the actual hosted journey: account setup/import, save, relaunch/sign-in,
   build plan, inspect explanations, record an **opt-in paper** action, stale
   plan, rebuild, history, and export. Verify values across browsers. Do not
   infer this from the disposable SQL test alone.
5. Before making performance claims, resolve the documented historical-price
   gaps (ANSS, ATVI, EA, SGEN, SPLK, WBA) and repeat valid walk-forward
   validation. Missing constituents must not be silently dropped.

## Working-tree review plan

This checkout is on `main`, one commit ahead of `origin/main`, with 22 modified
tracked files and over 100 untracked source/docs/test files. The website,
public API, and Supabase migrations are presently untracked. Review the dirty
tree in bounded groups before any staging: (1) website/API/cloud schema and
their tests, (2) current scanner and daily assistant, (3) historical data and
benchmark safety, (4) strategy/research changes, then (5) docs/config.
Generated caches, SQLite portfolios, local environment files, and executable
builds must stay ignored. This document is an inventory, not authorization to
stage, commit, push, migrate production, or publish.
