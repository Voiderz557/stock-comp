# Project documentation

Start with the [main README](../README.md) for entrypoints and setup. This folder
collects product plans and research evidence; their historical statements are
not a guarantee of current deployment or trading performance. Unless stated
otherwise, run commands from the repository root, not this folder.

## Product and architecture

- [Master roadmap](PROJECT_MASTER_ROADMAP.md) — priorities and completion criteria.
- [Public app architecture](PUBLIC_APP_ARCHITECTURE.md) — identity and data isolation.
- [Development plan](DEVELOPMENT_PLAN.md) — earlier vision and longer-term ideas.
- [Market watcher](MARKET_WATCHER.md) — local read-only change detection.

## Research and validation

- [Validation status](VALIDATION_STATUS.md) — recorded checks and limitations.
- [Historical data gaps](HISTORICAL_DATA_GAPS.md) — missing histories and sourcing requirements.
- [Strategy research roadmap](STRATEGY_RESEARCH_ROADMAP.md) — trials and promotion gates.
- [Research trial ledger](RESEARCH_TRIAL_LEDGER.csv) — recorded trials, including rejected variants.
- [ML research plan](ML_RESEARCH_PLAN.md) — feature timing and walk-forward validation.
- [Release readiness](RELEASE_READINESS.md) — remaining release requirements.

## Setup guides kept with their code

- [Website](../web/README.md)
- [Cloud database](../supabase/README.md)
- [Hosted website adapter](../deployment/chatgpt-site/README.md)
- [Python API and free Render preparation](../deployment/python-api/README.md)

## Repository boundaries

Application package names, import paths, root launchers, and desktop build specs
have not moved. Portfolio databases, account history, secrets, caches, known-working
executables, and the independently managed Sites checkout remain in their existing
locations and are not documentation or source-cleanup targets.

The legacy Cursor coordinator, its regression tests, Cursor-only rules, and the
outdated copy-paste mission prompt are archived outside this repository. Their
original content is recoverable, including the coordinator's own role instructions.
Existing coordinator task reports under LocalAppData are untouched. This repository
does not require Cursor tooling to run the trading app or its tests.
