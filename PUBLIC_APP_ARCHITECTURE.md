# Stock Competition Assistant — Public App Architecture

Status: proposed implementation baseline

Scope: public multi-user website first, desktop application second
Trading scope: research and paper trading only; no broker execution

## 1. Product decision

Build one shared product platform with two clients:

1. A responsive web client released first.
2. A Windows desktop client released later against the same backend and data model.

Keep the current Streamlit desktop application working as the internal research, backtesting, and migration source while the public client is built. Do not turn the existing Streamlit interface directly into the public multi-user product: its embedded authentication constraints and server-oriented execution model make it a poor long-term security boundary for an installable public application.

The proposed baseline is:

- Web: Next.js as the first public client.
- Desktop: Tauri 2 with React and TypeScript after the web product is validated.
- Authentication and primary database: Supabase Auth and Postgres.
- Strategy and plan service: Python FastAPI, reusing the existing tested Python domain logic.
- Market data: a server-side provider adapter with an explicitly licensed production provider.
- Packaging: signed Windows installer with a signed update channel.

This preserves the valuable parts of the current project—the strategies, portfolio rules, paper-trading behavior, plan history, and tests—without exposing Python, database credentials, or market-data credentials inside a public executable.

## 2. System boundaries

```text
Windows desktop (Tauri/React)        Website (Next.js)
             |                              |
             +---------- HTTPS/JWT ---------+
                            |
                    Python FastAPI API
                   /         |          \
          strategy engine  Supabase    market-data
          and plan logic    Postgres     provider
                            + Auth
```

### Public, untrusted clients

The desktop and web clients may contain only public configuration such as API base URLs and Supabase publishable identifiers. They must not contain:

- Supabase service-role keys.
- OAuth client secrets intended for confidential servers.
- Market-data provider secrets.
- Database credentials.
- Broker credentials.
- Signing keys.

### Trusted backend

FastAPI is the trusted computation boundary. It validates the authenticated user's JWT, obtains user-scoped data, calls the existing Python strategy and plan code, accesses licensed market data, and records idempotent paper-trading actions. Administrative credentials remain server-side.

### Database authorization

Every user-owned table includes `user_id`. Row Level Security is enabled and tested for every operation. The API performs authorization too, but RLS is the fail-safe boundary if an application query is wrong.

## 3. Authentication design

### Initial providers

- Google OAuth.
- Microsoft OAuth.
- Email magic link as an account-recovery and low-friction fallback.
- MFA can be added before broad public release; it should be required for administrative users.

Passwords should not be implemented by the application itself. If password authentication is later enabled, use the identity provider's implementation and controls rather than storing or hashing application-managed passwords.

### Desktop OAuth flow

1. The desktop client creates a cryptographically random PKCE verifier, challenge, state, and nonce.
2. It opens the system browser to the Supabase authorization URL.
3. The provider returns an authorization code through a registered loopback callback on `127.0.0.1` or a registered application deep link.
4. The client validates state and nonce, then exchanges the code using the PKCE verifier.
5. The short-lived access token remains in memory.
6. The refresh token is protected by Windows credential storage/DPAPI, never plain JSON, SQLite, logs, or local storage.
7. Sign-out revokes the session where supported and removes the protected local token.

The desktop binary is a public OAuth client and therefore must not rely on a reusable client secret. OAuth runs in the user's system browser, not an embedded webview.

### Web OAuth flow

The web application uses Supabase's server-side PKCE flow with secure, HTTP-only, SameSite cookies. Callback URLs are explicitly allow-listed per environment. Production does not use wildcard redirects.

### Identity safety rules

- Never automatically merge two provider identities solely because their email text matches.
- Require verified email where provider behavior supports it.
- Record provider identity and linking events in an audit trail.
- Support device/session revocation and account deletion.
- Rate-limit authentication-adjacent endpoints.

## 4. Core data model

All IDs are UUIDs. All timestamps are timezone-aware UTC. Monetary values use fixed precision decimals, not binary floats.

### `profiles`

- `user_id` — primary key and reference to the identity provider user.
- `display_name`.
- `created_at`, `updated_at`, `deleted_at`.
- `terms_version`, `terms_accepted_at`, `privacy_version`.

### `competition_accounts`

- `id`, `user_id`, `name`.
- `starting_capital`, `cash`.
- `snapshot_as_of`.
- `portfolio_version` — incremented after every accepted state change.
- `created_at`, `updated_at`.

### `positions`

- `id`, `user_id`, `account_id`.
- `ticker`, `direction` (`LONG` or `SHORT` where existing competition rules permit).
- `quantity`, `average_price`, `exact_cost_basis`.
- `opened_at`, `closed_at`, `status`.
- `source` (`ACCOUNT_SNAPSHOT` or `PAPER_ACTION`).
- Unique active-position constraint appropriate to account, ticker, and direction.

Imported holdings remain account snapshots. The migration must not invent trades, deposits, balancing entries, or historical profit and loss.

### `plans`

- `id`, `user_id`, `account_id`.
- `generated_at`, `market_as_of`, `portfolio_version`.
- `strategy_name`, `status`.
- `stale_at`, `stale_reason`.
- `portfolio_snapshot` JSONB containing the immutable input summary used for that plan.
- `warnings` JSONB.

### `recommendations`

- `id`, `user_id`, `plan_id`.
- `ticker`, `recommended_action`, `score`, `signal`, `reason`.
- `factor_details` JSONB.
- `rank`, `status`.

### `recommendation_events`

- `id`, `user_id`, `recommendation_id`.
- `event_type` (`DISMISSED`, `RECORDED`, `REOPENED`).
- `created_at`.
- Optional user note treated as untrusted text.

### `paper_actions`

- `id`, `user_id`, `account_id`.
- `plan_id` and `recommendation_id`, nullable when the user records an independent paper action.
- `action_type`, `ticker`, `direction`, `quantity`, `price`, `occurred_at`.
- `idempotency_key` with a per-user uniqueness constraint.
- `portfolio_version_before`, `portfolio_version_after`.

### `portfolio_snapshots`

- `id`, `user_id`, `account_id`, `as_of`.
- `cash`, `portfolio_value`, `long_exposure`, `short_exposure`.
- `positions_snapshot` JSONB.
- Missing valuations remain `null` with a warning; they are not replaced with zero or entry-price estimates.

### `import_jobs`

- `id`, `user_id`, source checksum, source schema version.
- `status`, preview summary, warnings, confirmed_at.
- Unique constraint on user and source checksum for idempotent migration.

### `audit_events`

- `id`, `user_id`, `event_type`, `request_id`, `created_at`.
- Minimal non-secret metadata required for security and support.

Audit logs must never contain tokens, OAuth codes, API keys, full database paths, or unnecessary portfolio payloads.

## 5. API surface

Initial versioned endpoints:

- `GET /v1/health` and `GET /v1/version`.
- `GET /v1/me`.
- `GET /v1/accounts` and `GET /v1/accounts/{account_id}`.
- `POST /v1/accounts/import-preview`.
- `POST /v1/accounts/import-confirm`.
- `PATCH /v1/accounts/{account_id}/snapshot`.
- `GET /v1/accounts/{account_id}/positions`.
- `GET /v1/accounts/{account_id}/actions`.
- `POST /v1/accounts/{account_id}/actions` for paper actions only.
- `POST /v1/accounts/{account_id}/plans` with an idempotency key.
- `GET /v1/accounts/{account_id}/plans/current`.
- `GET /v1/accounts/{account_id}/plans`.
- `GET /v1/plans/{plan_id}/comparison`.
- `POST /v1/recommendations/{recommendation_id}/events`.
- `GET /v1/market/regime`.
- `GET /v1/strategies`.

All mutating requests require an idempotency key and expected portfolio version. Version conflicts return a clear conflict response rather than silently overwriting newer state.

Plan generation records the portfolio version, market timestamp, price timestamps, algorithm version, and warnings. A plan becomes stale when its input portfolio version changes, required prices age beyond policy, or its configured time boundary passes.

## 6. Desktop application screens

1. Sign in / create account.
2. Terms, privacy, and paper-trading disclaimer.
3. Import existing local account with preview and confirmation.
4. Dashboard: value, cash, exposure, capacity, stale/missing-data warnings.
5. Today's plan: strategy, regime, ranked recommendations, reasons, and factor details.
6. Portfolio: positions, valuation timestamps, unrealized paper P&L when prices exist.
7. Paper action review and confirmation.
8. Plan history and current-versus-previous comparison.
9. Closed trades and recent actions.
10. Recommendation history, including dismissed and recorded items.
11. Account snapshot editor with review-before-save.
12. Settings: profile, sessions/devices, data export, deletion, diagnostics, and version.

No recommendation executes automatically. Paper actions always require an explicit user confirmation screen. The AI advisory layer remains disabled until separately reviewed and must never receive execution capabilities.

## 7. Existing local-data migration

The working executable and `%LOCALAPPDATA%\StockComp` data remain untouched during development.

At first authenticated launch:

1. Detect the existing SQLite database without modifying it.
2. Create a timestamped local backup before any migration read is accepted.
3. Read through a versioned migration adapter.
4. Show a preview: account snapshot, cash, holdings, plans, closed trades, preserved fields, missing fields, and warnings.
5. Ask the user to confirm the destination cloud account.
6. Upload an idempotent import using the source checksum.
7. Read the cloud result back and reconcile counts and totals.
8. Mark migration successful only after reconciliation.
9. Keep the original local database and backup. Never delete them automatically.

Incomplete local history must be labeled accurately. Current recommendation statuses and closed trades are not represented as a complete historical action ledger unless the underlying data proves that they are.

## 8. Market data and algorithm execution

The existing Python strategies remain the source of truth. They move behind an explicit service interface with regression fixtures proving unchanged outputs.

Production market-data requirements:

- Server-side credentials only.
- A license that permits the intended public-app usage and any displayed data.
- Point-in-time universe support where historical claims require it.
- Known-listing boundaries, failure caching, request timeouts, and coverage reporting.
- No substitution of today's constituent list for historical membership.
- Results with required data gaps are marked incomplete and cannot be promoted as valid.

The installed Massive research connector is useful for assisted research, but it is not automatically a runtime data license for the public application. Binance is out of scope unless crypto is deliberately added later.

## 9. Security requirements

- Separate development, staging, and production projects, OAuth applications, databases, keys, and domains.
- RLS policies and automated cross-user isolation tests for every user-owned table.
- JWT signature, issuer, audience, expiry, and revocation validation.
- PKCE, state, nonce, replay protection, and strict callback allow-lists.
- No secrets in desktop bundles, browser code, crash logs, analytics, or source control.
- Input validation and output encoding for notes, reasons, tickers, and imported records.
- Request size limits, timeouts, rate limits, and abuse controls.
- Content Security Policy and a restricted Tauri capability allow-list.
- Signed Windows binaries and signed updates.
- Dependency, secret, and vulnerability scanning in CI.
- Encrypted transport and provider-managed encryption at rest.
- Tested backups, restore procedures, account export, and account deletion.
- Privacy policy, terms of service, paper-trading disclaimer, and market-data attribution.

Before authentication code is accepted, install and run a dedicated security-review workflow. The Codex Security plugin is a strong fit for this phase; observability tooling such as Datadog or Sentry is optional but recommended before beta.

## 10. Reliability and observability

- Structured logs with request and job IDs, excluding secrets and unnecessary personal data.
- Metrics for sign-in failures, API latency, plan-generation duration, data coverage, stale plans, idempotency conflicts, and background-job failures.
- Explicit user-facing states for offline mode, token expiry, provider outage, missing market data, and plan-generation failure.
- Background scans use durable jobs with progress stages and cancellation.
- The desktop client can read cached last-known state while offline but cannot present it as current.
- Feature flags and server-side minimum-client-version controls enable safe rollout.

## 11. Build and release environments

### Local development

- Local Tauri/React client.
- Local FastAPI server.
- Supabase local stack or isolated development project.
- Fake market-data provider for deterministic tests.
- Synthetic portfolio data only.

### Staging

- Separate Supabase project, OAuth clients, API deployment, and data-provider credentials.
- Signed test build distributed only to testers.
- No production portfolio data.

### Production

- Production domain and OAuth redirect allow-list.
- Signed Windows installer and updater artifacts.
- Protected CI release keys.
- Monitored API and database.
- Documented rollback and minimum-supported-version policy.

## 12. Release phases and gates

### Phase 0 — decisions and contracts

- Choose app name, domain, OAuth providers, hosting region, market-data provider, legal owner, support contact, and release channel.
- Freeze API contracts and migration rules.

Gate: architecture and data-license review approved.

### Phase 1 — backend foundation

- Supabase environments, schema, migrations, RLS, Auth, FastAPI skeleton, JWT validation, and CI.

Gate: automated cross-user access tests prove isolation; no service secret appears in either client.

### Phase 2 — migration and account state

- Read-only local import, preview, backup, confirmation, cloud reconciliation, and rollback-safe retry.

Gate: migration round-trip succeeds on copied test databases and never changes source data.

### Phase 3 — web vertical slice

- Login, dashboard, portfolio, plan generation, explanations, plan history, and confirmed paper actions.

Gate: token expiry, responsive behavior, stale plans, duplicate actions, missing prices, and session persistence pass.

### Phase 4 — web hardening

- Threat model, external security review, rate limits, audit logging, backup restore, and production deployment controls.

Gate: no unresolved critical/high security findings; rollback tested.

### Phase 5 — private beta

- Invite-only users, staged rollout, support process, telemetry review, and data-quality monitoring.

Gate: reliability targets and support readiness met.

### Phase 6 — public web release

- Public onboarding, legal pages, account export/deletion, incident response, and production support.

### Phase 7 — Windows desktop application

- Tauri client using the existing API, schema, React design system, and authentication project, followed by signed installer and updater work.

Gate: desktop and web produce consistent account, plan, and paper-action state.

## 13. Required verification

- Existing Python strategy and backtest regression suite remains unchanged.
- OAuth callback, state, nonce, PKCE, expiry, refresh, logout, and revoked-session tests.
- Cross-user read/write denial tests at both API and database layers.
- No duplicate paper action under retries, double-clicks, reconnects, or concurrent devices.
- Plan invalidation when account state changes.
- Missing valuations remain null with explicit warnings.
- SQLite import backup, preview, confirmation, idempotency, and reconciliation tests.
- Desktop launch, sign-in, close, relaunch, update, and persisted-session tests.
- Production build inspection for secrets and writable access outside approved app-data locations.
- Data-provider outage and incomplete-coverage tests fail closed.
- Account export and deletion tests.

## 14. Decisions required before implementation

The following choices require owner approval because they affect cost, contracts, or public identity:

- Final product name and domain.
- Google only, Microsoft only, or both at launch.
- Supabase region and paid tier.
- FastAPI host and worker/job host.
- Production market-data vendor and redistribution/display rights.
- Monitoring and error-reporting vendor.
- Windows code-signing route and publisher identity.
- Privacy policy, terms, disclaimer, data-retention period, and deletion SLA.
- Invite-only beta size and whether any paid subscription exists.

Do not begin public authentication or cloud-data implementation until the market-data license, legal owner, and environment separation are decided.

## 15. Architecture decisions

- ADR-001: Next.js is the first public client. Tauri/React follows after web validation; the current PyInstaller/Streamlit build remains the working legacy/internal application during migration.
- ADR-002: Supabase Auth and Postgres provide shared identity and user-scoped persistence.
- ADR-003: FastAPI hosts existing Python strategy and plan logic; clients never run privileged strategy services or hold provider secrets.
- ADR-004: Streamlit remains an internal research/backtesting interface, not the public authenticated frontend.
- ADR-005: Version 1 is advisory and paper trading only.
- ADR-006: Existing SQLite data moves only through explicit previewed, backed-up, idempotent import.
- ADR-007: The website reuses the backend and contracts rather than becoming a separate product stack.

## 16. Reference standards

- OAuth for native apps: RFC 8252 and Authorization Code with PKCE.
- Supabase Auth, PKCE, redirect allow-lists, and Postgres Row Level Security.
- Tauri capabilities, webview security, signing, and updater guidance.
- OWASP authentication, session, and secure storage guidance.
- Microsoft Windows data-protection and code-signing guidance.
