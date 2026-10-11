# Stock Competition web prototype

This is a research/paper-trading web prototype. It supports isolated demo mode and optional Supabase sign-in. Signed-in users can create an account snapshot, update holdings, generate a daily plan, compare saved plans, and dismiss or restore current recommendations. The FastAPI service and Supabase store these cloud records; they do not read the local SQLite portfolio or execute trades. The older Windows desktop executable is separate and unchanged.

Run locally:

```powershell
cd web
npm install
npm run dev
```

Then open `http://localhost:3000`.

To run the public API in another terminal using the browser-safe values from
`web/.env.local`:

```powershell
python -m pip install -r requirements-web-api.txt
python -m api.run_local
```

The local launcher copies only the Supabase URL, publishable key, and demo-mode
flag. It never loads OAuth client secrets or a Supabase service-role key.

Copy `.env.example` to `.env.local` only when configuring local public values. Apply the SQL migrations in `supabase/migrations/` before using cloud accounts; the latest migration adds estimated-close paper actions and history. The browser needs the Supabase URL and publishable key; the API needs its corresponding server-side environment variables. Production authentication and persistence must follow `../PUBLIC_APP_ARCHITECTURE.md`. Do not add service-role keys, market-data secrets, or other privileged credentials to `NEXT_PUBLIC_*` variables.

`bizwax.org` is the intended public domain, but no public host or DNS record has been selected or deployed by this repo. Once the host is chosen, set `NEXT_PUBLIC_SITE_URL` to its exact HTTPS origin, add that origin and `/auth/callback` URL to Supabase's site/redirect allow list, add the origin to the Google Web OAuth client's Authorized JavaScript origins, and test login/logout on that host. DNS, HTTPS, and the hosting provider must be configured separately. Do not point OAuth redirects at the current localhost URL for public launch.

### Social sign-in setup

The primary Google button uses Google Identity Services when
`NEXT_PUBLIC_GOOGLE_CLIENT_ID` is set to the **public** Web OAuth client ID
already configured in Supabase. Add the exact website origin (including port
for local development) to that client's Authorized JavaScript origins in
Google Cloud. The Google ID token is exchanged for a Supabase session with a
fresh nonce; it is not logged or sent to the competition API. The old Google
redirect remains available as a fallback. If the direct button cannot load,
check the origin and Google Cloud browser console errors. Google Branding and
Verification can display the app name/logo on Google's consent screen, but
the fallback redirect still shows `<project-id>.supabase.co` on the free
Supabase plan. Do not enable a paid custom auth domain without approval.

Microsoft sign-in requires a Microsoft Entra app registration; code alone
cannot enable it. Register a Web app with **Accounts in any organizational
directory and personal Microsoft accounts** so both work/school and personal
accounts are accepted. Set its redirect URI to
`https://<your-supabase-project-ref>.supabase.co/auth/v1/callback`, then enter
the application/client ID and a **client secret directly in the Supabase
Dashboard** (Authentication → Providers → Azure). Do not put the secret in
this repo or browser environment. The website already requests the required
`email` scope and enables the Microsoft button only after Supabase reports
Azure as active. Configure your website's `/auth/callback` URL in Supabase's
redirect allow list. Test with an account of each intended type before
offering public sign-in. Do not assume Microsoft and Google accounts with
the same email are automatically linked.

The plan builder defaults to **All strategies — consensus**, checking all six
live-enabled strategies on dated stock/benchmark history. Controlled Breakout V1
remains research-only. A candidate needs at least two BUY votes (three during
HIGH_VOLATILITY), at most two AVOID votes, and a BUY from a regime-preferred
strategy. DOWNTREND does not force a long purchase. Consensus score is 70% BUY
vote share + 20% mean within-strategy BUY-rank strength + 10% preferred BUY vote
share. Raw strategy scores are shown for context but never added together.
Missing strategy output excludes that stock and produces a warning. A stock
quote older than the latest available SPY/QQQ session cannot become a new BUY
allocation; stale holding quotes are flagged and block new allocations until
shared exposure can be marked to the same session.

Choose a $5k/$10k/$15k/$20k per-stock *planning* target; cash, shared capacity,
the $5 price floor, and the existing $20k stock cap still apply. Whole-share
estimates use the last dated close and are not orders or guaranteed fills.

The website now has one Competition-style workspace. Settings (strategy,
current-day scanner universe, per-stock planning target, minimum-price mode, and optional exit guidance) are
saved in the existing user-scoped `planning_profiles` storage under "App
settings". Older named profile records and their rules remain intact for
account export, but are no longer selectable in the main UI. Saving settings
requires the `20261007120000_planning_profiles.sql` migration; the read-only
defaults still work without it. "Any eligible price" adds **no discretionary
minimum**: the official $5 stock-price floor still applies, and the ranking
algorithm chooses among eligible stocks. An explicit no-minimum or custom
below-$5 setting creates a research-only plan; sub-$5 BUY ideas cannot be
recorded as competition paper actions. The default 139-symbol current-day
watchlist can be expanded to an opt-in 224-symbol broad watchlist. Neither
list changes historical point-in-time benchmark membership, and broader scans
may take longer or have unavailable quotes. Changing either setting takes
effect when the plan is rebuilt, never by silently rewriting an existing plan.
The Research page shows a separately dated VIX reading; it does not modify
strategy scores or the SPY/QQQ realized-volatility regime rule. See
`../PROJECT_MASTER_ROADMAP.md` for the larger implementation and validation plan.

Optional stop-loss and take-profit toggles display **advisory SELL levels**
when inspecting a current BUY recommendation. The read-only dated-data endpoint
uses ATR(14) and the saved price session; it refuses stale or mismatched
quotes. The stop distance is `2 × ATR(14) / close`, bounded to 3–12%, with a
2R take-profit level. It is not a live order, automatic sell, or guaranteed
loss limit. A TradingView Paper Trading user must attach and verify linked
exits manually; a gap can fill below the stop trigger. The old profile API and
manual ticket formatter remain for compatibility, but the separate profile/
TradingView workspace is no longer part of the website navigation.
The new client uses `/v1/plans/exit-guidance`; the old auto-plan route remains
as a compatibility alias.

Settings now offers a read-only JSON download of all application-table rows visible to the signed-in user through Supabase RLS. It includes account snapshots, positions, saved plans, recommendation events, paper actions (if any), planning profiles, and audit records. It excludes identity-provider credentials and is not yet an import format. The export is paginated but not transactionally frozen, so concurrent edits may span pages; any failed page aborts the whole download. Keep this file private because it contains personal portfolio data. Account deletion is not yet self-service.

Cloud paper actions are opt-in and paper-only. Apply `20261008110000_recommendation_paper_actions.sql` before enabling recording; until the migration is present, the UI disables the confirmation button. The user must review a BUY or SELL recommendation and enter a whole-share quantity. A saved, dated daily close is used as an **estimate**, not a broker fill or live quote. The server checks ownership, the current plan and portfolio version, quote freshness for every holding, cash, shared gross capacity, the per-stock cap, and an idempotency key in one transaction. Successful actions update paper cash/holdings, record a ledger row, and stale the plan; generate a new plan before another action. Only LONG recommendations are supported. Account snapshots are not converted into fake trades, and no external broker orders are sent. Recent actions are displayed in History; the private account export contains the complete ledger. Live Supabase RLS behavior still requires verification before any public rollout.

New plans retain candidate-level diagnostics in their immutable plan input JSON, without changing scores or signals; the history view can then identify signal, agreement, data, or allocation reasons why a ticker did not reappear. Older plans without those diagnostics show the previous saved signal and relevant warnings but mark the exact removal cause unknown. A plan is marked stale after an account snapshot update; stale plan-derived recommendations and regime are hidden from the current API response. Prices and recommendation details retain their own market-data timestamps.
