# Cursor → Codex coordinator

A small sequential coding/review coordinator. It is **not** part of the paper-trading application and never places trades, commits, pushes, merges, or publishes.

Cursor implements. Codex reviews the actual uncommitted diff. Findings go back to Cursor. The loop stops after approval, a blocker, an authentication/usage-limit failure, malformed review output, or 3 rounds.

## Isolation

- Task files and worktrees default to `%LOCALAPPDATA%\StockCompCoordinator\` (override with `STOCK_COMP_COORDINATOR_ROOT`).
- Live portfolio data in `%LOCALAPPDATA%\StockComp\`, `paper_trading_data/`, `*.sqlite3`, `.env`, `auth.json`, and `dist/` executables stay outside the agents’ write root.
- Each task uses `git worktree add -b` from `HEAD`. Uncommitted source files are listed and **not** copied, stashed, reset, or discarded.
- Child processes have `CURSOR_API_KEY` and `OPENAI_API_KEY` stripped so the coordinator does not enable paid API fallback.

## Commands

```powershell
python -m coordinator diagnose
python -m coordinator run --task "..." --criterion "..." --source-repo PATH
python -m coordinator resume TASK_ID
python -m coordinator stop TASK_ID
python -m coordinator status TASK_ID
```

`--data-root` is accepted on every subcommand. Default reports live under `%LOCALAPPDATA%\StockCompCoordinator\tasks\<task-id>\report.md`.

`--implementer fake --reviewer fake` verifies orchestration without calling real CLIs.

Reports: `%LOCALAPPDATA%\StockCompCoordinator\tasks\<task-id>\report.md`

## Remaining CLI setup

Run `python -m coordinator diagnose` for the current machine status. The Cursor
editor CLI is not the same as the Cursor Agent CLI.

Install and log in with your existing Cursor and ChatGPT subscriptions. Do not add API keys.

### Cursor Agent CLI

```powershell
irm 'https://cursor.com/install?win32=true' | iex
agent --version
agent login
agent status
```

Use browser login. Do not set `CURSOR_API_KEY`.

### Codex CLI

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://chatgpt.com/codex/install.ps1 | iex"
codex --version
codex login
codex login status
```

Choose **Sign in with ChatGPT**. Do not configure an OpenAI API key fallback.

## Cloud later

The adapters, data-root env var, and saved handoff files (`status.json`, `rounds/`, `report.md`) are local-first but portable. A later host can run the same `python -m coordinator` entry point against a remote checkout and the same worktree layout.
