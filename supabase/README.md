# Supabase cloud foundation

The migration in `migrations/` creates the public-app account model, enables Row Level Security on every user-owned table, revokes anonymous access, and exposes one security-invoker snapshot function.

The website never receives a service-role key. Read-only account calls and ordinary checked account actions use the verified user's token and RLS. The trusted-plan-writer migration is an exception for **saving generated plans only**: the backend must use a server-only service-role key after verifying the user's JWT and constructing the plan. It revokes the browser-callable plan writer and marks existing current plans stale; plan history and account data are retained. No paid AI or market-data API is added by this change.

## Apply to a development project

Do this only after creating a dedicated **development** Supabase project:

```powershell
npx supabase login
npx supabase link --project-ref YOUR_DEVELOPMENT_PROJECT_REF
npx supabase db push --dry-run
npx supabase db push
```

Use browser login and never paste tokens into source files or chat. Do not run `db reset --linked` against production.

## Configure OAuth

In the Supabase dashboard:

1. Set the site URL and explicitly allow `/auth/callback` for the development domain.
2. Configure Google and/or Microsoft (Azure) providers using provider-owned client credentials.
3. Put only the Supabase project URL and publishable key in `web/.env.local`.
4. Put the same URL and publishable key in the API's server environment as `STOCK_COMP_SUPABASE_URL` and `STOCK_COMP_SUPABASE_PUBLISHABLE_KEY`.
5. After reviewing and applying `20261008120000_trusted_plan_writer.sql`, set `STOCK_COMP_SUPABASE_SECRET_KEY` to a dedicated `sb_secret_*` key **only in the API server's private environment**. A legacy JWT-based `STOCK_COMP_SUPABASE_SERVICE_ROLE_KEY` also works temporarily, but the new secret key is preferred. The plan-generation endpoint fails closed with 503 until one is set; do not put either key in `web/.env.local`, a `NEXT_PUBLIC_*` setting, the repository, or a packaged desktop client. Restart the API after setting it and generate a fresh plan. Existing saved plans remain in history but are no longer actionable.
6. Set `NEXT_PUBLIC_DEMO_MODE=false` only after the migration is applied and OAuth redirects are verified.

Never expose the Supabase service-role key, database password, OAuth client secret, or market-data credentials to the browser.

## Required validation before staging

Run the local Supabase stack with Docker and execute:

```powershell
npx supabase start
npx supabase db reset
npx supabase test db
```

The checked-in SQL test proves that one user cannot read or create records for another user. This machine currently needs Docker and an authenticated Supabase CLI before those database-level tests can run.
