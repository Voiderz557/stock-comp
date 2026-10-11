# Stock Competition on ChatGPT Sites

This is the GitHub-tracked source checkpoint for the Site adapter. The native
Sites checkout under `../../sites/stock-comp-private-preview/` has its own Git
history and is intentionally excluded from this repository (not a submodule).
Run `node scripts/sync-frontend.mjs` here before installing/building to generate
the ignored frontend files from the tracked `../../web` source. When publishing,
open the existing Site through its native source workflow and copy only reviewed
adapter/source files; never copy Git metadata, local config or user data.

Hosted URL: https://stock-comp-private-preview.voider123.chatgpt.site

This source reuses the existing `../../web` frontend: account setup/editor,
portfolio, daily plan, strategy explanations, share proposals, optional exit
guidance, history/comparison, recommendation dismissal, explicit paper-action
confirmation, account export, adjustable settings and legal pages. The Site
remains private. Its original visual preview is retained as deployment version
1 for rollback; the next version replaces the disconnected mock screens.

## Account connection

The Worker uses the **same Supabase project and authenticated user** as the
existing website. No cloud account rows are copied, replaced, or reassigned.
Signing in with the same provider restores the same account. ChatGPT Site
access and the existing Supabase account sign-in are separate checks. Supabase
OAuth redirects include the hosted callback as well as the local callbacks.
Microsoft remains disabled unless that provider is enabled in Supabase.

All account calls carry the user's token and the publishable key to the existing
RLS-protected database and checked RPCs. The gateway has no service-role key,
plan writer, or broker connection. Existing atomic account updates, before/after
snapshot backups, version conflicts, idempotency, share/capacity validation and
plan staleness are enforced by the existing database functions. Unknown prices
remain null. The missing paper-action and trusted-writer migrations were
applied to the existing development project on 2026-10-11; legacy saved plans
are preserved and marked stale until rebuilt by the trusted server.

Runtime variables in Sites:

- `STOCK_COMP_SUPABASE_URL` — public project origin.
- `STOCK_COMP_SUPABASE_PUBLISHABLE_KEY` — public key, never a server secret.
- `STOCK_COMP_PYTHON_API_URL` — future HTTPS Python service origin.

The browser initializes from `/api/v1/config` before restoring its Supabase
session. Account APIs use the same-origin `/api/v1` gateway. Nothing points to
a user's localhost in the hosted build. Request/response sizes, timeouts,
accepted paths, redirects and auth failure behavior are bounded; account
responses are never cached. Gateway per-user request limits are per Worker
isolate; the Python host has the original API limiter too.

## Strategy service setup is outstanding

The unchanged Python strategy engine cannot execute inside the Sites
JavaScript Worker. Until an HTTPS Python service is connected, the Site says
that daily scans, VIX downloads, and automatic ATR exit guidance need service
setup. Those requests return an explicit unavailable response; they never
manufacture results or save caller-provided plans.

The free Render container and Blueprint are prepared at
`../../deployment/python-api/`. A Render account, reviewed source delivery,
and a server-only Supabase plan-writer key on Render are still required.
After service health and authenticated planning are verified, configure its
actual HTTPS origin as `STOCK_COMP_PYTHON_API_URL` and redeploy a saved Site
version to apply the env revision. Do not put the trusted key in Sites or the
browser. Free Render sleeps when idle and broad scans need real resource tests.

Local desktop SQLite accounts/history/builds remain untouched. They have not
been transferred into the cloud account: their lot IDs/history differ from the
cloud schema, and choosing an authoritative source and reviewing an import are
required before replacing an account. Do not represent snapshot imports as
historical trades, replay trades, or mark legacy plans trusted/current.

## Rebuild and verify

After editing the shared frontend, synchronize the explicitly allowed source
directories with `node scripts/sync-frontend.mjs` from this checkout. This reads
the actual Python registry for strategy names; it copies no env files, databases,
caches, user records, or executable builds. Review the resulting Site diff.

Install dependencies with the Sites installer (on this Windows installation,
its npm shim failed; explicit `npm.cmd ci --no-audit --no-fund` succeeded).
Run `node --test tests/hosted-api.test.mjs` and `node scripts/build-hosted.mjs`.
The latter builds the existing Next static export and embeds its public assets
and the API gateway in the supported Worker ESM entrypoint. It does not port
strategy formulas. `node scripts/preview-hosted.mjs` serves a localhost preview
on port 4175 using only the existing public connection config.

Push/package/deploy through the Sites workflow for the existing project ID.
On Windows Git Bash, the archive path uses `/c/Users/...` for tar; the native
upload uses the equivalent absolute Windows path. Keep deployment source and
archive paired. V1 is available in Sites for rollback; the legacy desktop build
and local website remain available independently.

Verification distinguishes mock HTTP gateway tests and in-memory SQL tests
from real deployment, real OAuth, and real scans. Hosted scans are not verified
until Render setup is complete. Do not label this migration complete yet.
