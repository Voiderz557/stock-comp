# Portable Python API container

This packages the existing `api.public_app:app` FastAPI service and unchanged
Python strategy engine for a separate HTTPS container host.
[The release record](../../docs/live-plan-jobs-2026-10-15.md) documents successful
Free Render deployments and live plan save/reload verification on 2026-10-16,
including checkpoint `fe076b1`. Those are historical observations; live status
was not checked for this 2026-10-23 documentation update. No build, deployment,
account write, or benchmark was performed for this update.

## Safe build context

Run a future build from the **repository root**, using Docker with support for
Dockerfile-specific ignore files:

```powershell
docker build -f deployment/python-api/Dockerfile -t stock-comp-python-api .
```

The adjacent `Dockerfile.dockerignore` starts by excluding everything and
allows only named source files and requirements. Docker applies this file
before transferring the build context. Never omit or rename it independently
of the Dockerfile. See [Docker's build-context documentation](https://docs.docker.com/build/concepts/context/#dockerignore-files).
The Dockerfile deliberately copies only `config.py`, selected `api`, `data`,
`market`, `paper_trading`, and `strategies` sources, plus the launcher.

SQLite databases (including WAL/SHM files), portfolio data, account history,
saved plans, price caches, manually supplied historical files, env files,
credentials, `.git`, desktop builds, virtual environments, tests, frontend,
and Sites sources are excluded. New files are excluded until individually
reviewed and allowlisted. Do not use a host workflow that uploads the whole
working directory before applying Docker's filtering; provide a reviewed
source-only repository or an image built with this context instead. Do not
attach local portfolio directories as container volumes.

## Process and data behavior

The image uses Python 3.11, a non-root numeric user, and Uvicorn directly. It
binds `0.0.0.0` on the host-provided `PORT`, defaulting to `8000`; `EXPOSE` is
documentation, not a fixed listening-port override. Gunicorn is unnecessary
for a host that supervises the container. Keep one process/instance initially:
the API rate limiter and market-cache locks are process-local. Set edge rate
limits on the chosen host. Forwarded headers are not trusted by this launcher;
the host must enforce HTTPS and client-aware limits at its edge. Health
requests share the existing read limiter, so use moderate probe frequency.

`/v1/health` is an unauthenticated liveness route returning `status: ok`; it
does not prove that authentication, database permissions, or market providers
are ready. The Docker health probe honors `PORT` and needs no curl package.
Graceful shutdown allows 170 seconds, although a hosting platform may impose a
shorter termination window. The legacy synchronous plan deadline is 150 seconds;
asynchronous jobs have a 600-second deadline and process-local progress, so a
shutdown can interrupt a running job. This is not a durable job queue.

Market prices are downloaded on demand through the existing provider code.
`STOCK_COMP_CACHE_DIR` points at writable `/tmp/stock-comp/market-data`; yfinance
also has a writable home/cache directory. Caches can disappear on restart.
Historical file fallback remains in the unchanged engine, but no local
historical files are shipped. Missing provider history remains unavailable;
the container does not substitute user files or change strategy formulas.
Cloud accounts, profiles, plan history, and paper actions use the existing
Supabase adapter. Local SQLite storage/execution modules are not shipped.
This service supports paper trading only.

The deployment requirements extend `requirements-web-api.txt` with pandas,
yfinance, and PyArrow. Desktop Streamlit, plotting, ML training, and benchmark
dependencies are unnecessary. Versions have bounded ranges, not a complete
transitive lock; review and lock a tested Linux image before a production
release requiring reproducible dependencies.

## Remaining hosting configuration

A new installation requires a host account, authorized source/image delivery,
and an HTTPS service URL. The existing deployment is recorded above; do not
create a duplicate service just to follow these setup instructions.
Configure server variables in the host's secret/env
settings (never Docker build arguments or files in the image):

- `STOCK_COMP_SUPABASE_URL` and `STOCK_COMP_SUPABASE_PUBLISHABLE_KEY` for the
  existing Supabase project and its authenticated storage adapter.
- `STOCK_COMP_SUPABASE_SECRET_KEY` **or** the existing legacy
  `STOCK_COMP_SUPABASE_SERVICE_ROLE_KEY` for trusted plan saving. Keep this
  credential exclusively on the Python host.
- `STOCK_COMP_ALLOWED_ORIGINS`: the actual HTTPS Sites origin, plus any
  separately authorized preview origins, comma-separated. No wildcard.
- `STOCK_COMP_HTTPS_ONLY=true` when the host provides HTTPS termination.
- Keep `STOCK_COMP_API_DEMO_MODE` unset or false for authenticated hosting.
  `STOCK_COMP_SUPABASE_JWT_AUDIENCE` defaults to `authenticated`.

The existing Supabase schema, RLS policies, auth/JWKS setup, and trusted-save
RPC must already be provisioned and compatible. Applying migrations or
importing account data is a separate operation. The Sites client still needs
the resulting API URL; this package does not change client configuration or
existing env files/keys.

## Free Render setup, when ready to deploy

`deployment/python-api/render.yaml` prepares one Free Docker web service with
repository-root context and health path `/v1/health`. It defines no paid
services, databases, disks, or credential values. All env entries contain
only a name and `sync: false`; Render asks for the values during initial
Blueprint creation. Enter the intended Sites HTTPS origin for that installation;
the Blueprint does not embed an origin or credentials. The Blueprint remains
outside the Docker source allowlist and is not copied into the image.

The following are initial-setup instructions for a new installation. For the
existing service, use its settings and the release record instead. This
documentation update does not create an account, publish source, or deploy anything:

1. Create a Render account at [Render](https://dashboard.render.com/) and sign
   in. Use a workspace suitable for Free compute; do not select paid compute
   or attach a disk. Connect the GitHub account that can access the intended
   source repository.
2. Confirm the selected GitHub branch already contains the reviewed API
   source, `config.py`, `requirements-web-api.txt`, all exact source inputs
   named by `Dockerfile.dockerignore`, and
   `deployment/python-api/render.yaml`. Keep the same repository-root layout
   in a source-only repository. The adjacent Dockerfile ignore file is
   mandatory. Local untracked changes and a configured Git remote do not make
   these files available to Render: any required commit/push is a separate,
   authorized publishing step. Review repository contents and history for
   secrets and runtime data before connecting it; Docker filtering cannot
   remove files already published to GitHub.
3. In Render choose **New > Blueprint**, connect that GitHub repository, and
   select the branch containing the prepared files. Enter
   `deployment/python-api/render.yaml` in **Blueprint Path**. Do not move it
   to the repo root. Docker paths are relative to the repository root, not
   the Blueprint directory. Leave the service's Root Directory unset.
4. Fill the initial env prompts in Render's UI:
   `STOCK_COMP_SUPABASE_URL` = the existing Supabase project URL;
   `STOCK_COMP_SUPABASE_PUBLISHABLE_KEY` = its publishable key;
   `STOCK_COMP_SUPABASE_SECRET_KEY` = the server-only secret;
   `STOCK_COMP_ALLOWED_ORIGINS` = the real Sites HTTPS origin
   (`https://` plus its hostname, no path or wildcard; comma-separated only
   for additional authorized origins);
   `STOCK_COMP_HTTPS_ONLY` = `true`;
   `STOCK_COMP_API_DEMO_MODE` = `false`.
   Never enter the trusted Supabase secret into Sites or commit it to YAML.
   If only a legacy service-role credential is available, use the existing
   `STOCK_COMP_SUPABASE_SERVICE_ROLE_KEY` name in place of the secret-key
   Blueprint entry before publishing; do not rename the application's env
   keys or require both credentials.
5. Review the proposed resources: exactly one web service,
   `stock-comp-python-api`, Docker, **Free**, and no disk or Render database.
   Check Dockerfile `deployment/python-api/Dockerfile`, Docker context `.`,
   and health path `/v1/health`. Leave the Docker start command override
   empty; startup comes from the Dockerfile CMD. Render normally supplies
   `PORT=10000`, which the launcher honors. The Blueprint disables service
   auto-deploys and gives shutdown 170 seconds. In the Blueprint settings,
   also disable **Auto Sync** if you want to review future YAML changes
   manually; service auto-deploy and Blueprint sync are separate controls.
6. Only when deployment is authorized, click **Deploy Blueprint**. Review
   build/start logs and confirm the service URL's `/v1/health` returns HTTP
   200 with `status: ok`. A health response alone does not verify Supabase,
   JWT validation, market scans, or account isolation. Check those flows
   using a disposable paper account before directing the Sites client to
   this API. Existing account migration requires its own verified procedure.
7. Record the actual Render HTTPS URL for the later Sites API configuration.
   For subsequent env changes, use the service's **Environment** settings;
   `sync: false` prompts occur only during initial Blueprint creation and
   do not apply new variables when an existing Blueprint is updated.

These fields and setup steps follow the official
[Render Blueprint setup](https://render.com/docs/infrastructure-as-code) and
[Blueprint YAML reference](https://render.com/docs/blueprint-spec), checked
2026-10-11. See also [Render web services](https://render.com/docs/web-services)
and [Docker support](https://render.com/docs/docker). The local tests validate
the YAML and its safety constraints. Later successful Render deployments are
recorded above; they do not establish current live health or validate every
future change to this package.

Render warns against Free instances for production: they sleep after 15
idle minutes, can take about a minute to wake, lose local files on restart,
and lack persistent disks. Monthly quotas and outbound-traffic limits apply.
See [Render Free limitations](https://render.com/docs/free) (checked
2026-10-11). The October 16 record confirms a successful 133.9-second BROAD
plan, not sustained capacity or a service-level guarantee. The website now uses
short start/status requests for asynchronous planning to avoid holding one
proxy connection through the scan. Confirm resource headroom, restart behavior,
and cold-start/request timeouts before claiming reliable hosted planning.

## Focused local verification

```powershell
.venv\Scripts\python.exe -m pytest tests/test_python_api_deployment.py tests/test_public_app_api.py tests/test_cloud_plan_service.py
```

The deployment tests check the exact context allowlist, sensitive-path
exclusions, dependency coverage, port behavior, launcher settings, and API
liveness/fail-closed authentication from an isolated source-only copy.
They neither fetch prices nor modify local accounts. A fresh image build and
runtime verification for subsequent changes require suitable tooling and an
authorized host; the October 16 deployment record is not a fresh check of them.
