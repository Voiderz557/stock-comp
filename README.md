# Stock Strategy Backtester

This project scans stocks and tests stock-selection strategies against a market
benchmark. The backtester supports reproducible random periods, point-in-time
Nasdaq-100 membership, local price caching, delisted-ticker failure reporting,
and side-by-side strategy comparison.

## Project structure

- `app/` — Streamlit backtest interface
- `strategies/` — strategy implementations and the strategy registry
- `backtesting/` — simulation engine, random periods, summaries, and ZIP export
- `data/` — historical universes, ticker identities, and market-data providers
- `scanner/` — current-market scanner and its chart dashboard
- `historical_data/` — optional manually supplied historical Parquet files
- `tests/` — regression tests
- `config.py` — user-configurable defaults and competition constraints

## Install

Create and activate a virtual environment, then install the project packages:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Run

Launch the Streamlit backtester from the project root:

```powershell
python -m streamlit run app/backtest_ui.py
```

Run one command-line backtest with the configured defaults:

```powershell
python -m backtesting.engine
```

Run the interactive single-stock dashboard:

```powershell
python main.py
```

Run the paper-trading competition dashboard (daily assistant):

```powershell
.\launch_competition_dashboard.bat
```

or, from the project root with `.venv` active:

```powershell
python -m streamlit run app/competition_dashboard.py
```

## Windows desktop app

Build a double-clickable windowed app (no extra terminal, bundled Python):

```powershell
.\build_desktop_app.bat
```

The executable is:

`dist\StockCompDashboard\StockCompDashboard.exe`

On this machine that is:

`c:\Users\evann\stock-comp\dist\StockCompDashboard\StockCompDashboard.exe`

Double-click it. It starts a localhost-only Streamlit server, waits until the dashboard answers, then opens that page in its own window. Closing the window stops the server.

Persistent cash, positions, plans, and actions are stored outside the `.exe` and packaging folders:

`%LOCALAPPDATA%\StockComp\paper_trading_data`

If an existing project `paper_trading_data` folder is found and the LocalAppData ledger does not exist yet, the app copies it there and also writes a timestamped backup under `%LOCALAPPDATA%\StockComp\backups`. The original project folder is left in place and is never overwritten.

Developers can still run the same wrapper from source:

```powershell
python -m desktop.launcher
```

The known-working build stays at the path above. The AI-enabled build is a separate folder:

```powershell
.\build_desktop_app_ai.bat
```

`c:\Users\evann\stock-comp\dist\StockCompDashboardAI\StockCompDashboard.exe`

Both builds use the same `%LOCALAPPDATA%\StockComp\paper_trading_data` ledger. Building the AI app does not overwrite the earlier `.exe` folder.

The manual-account desktop build is another separate folder:

```powershell
.\build_desktop_app_manual.bat
```

`c:\Users\evann\stock-comp\dist\StockCompDashboardManual\StockCompDashboard.exe`

## Manual competition account

Use **Set up / update competition account** to enter starting capital, current cash, holdings, and a snapshot date. Review the preview, then confirm. Holdings are stored as an account snapshot, not as fake trades. Closed-trade history is kept. A backup is written under the paper-data `backups` folder before the live account is replaced. Cash plus holdings cost basis must equal starting capital plus any realized P&L already stored from closed trades; the form will not invent balancing P&L.

## AI analysis (read-only)

AI analysis is hidden unless you set `STOCK_COMP_AI_ENABLED=1`. When shown, the dashboard can send a structured snapshot to an optional AI provider after you click **Analyze current state**. Ordinary page reruns do not call the provider. The model cannot record trades or change the ledger.

Configure from the environment — never put keys in source:

```powershell
$env:STOCK_COMP_AI_PROVIDER = "openai_compatible"
$env:STOCK_COMP_AI_MODEL = "gpt-4o-mini"
$env:STOCK_COMP_AI_API_KEY = "your-key"
# optional: $env:STOCK_COMP_AI_BASE_URL = "https://api.openai.com/v1"
```

`OPENAI_API_KEY` is accepted if `STOCK_COMP_AI_API_KEY` is unset. The snapshot excludes API keys, local paths, and extra account identifiers. Expand **Data sent to AI** to preview it. Replies are interpretation, not recorded facts or algorithm signals.

## Paper trading assistant

The competition dashboard is a local daily assistant. It does not place broker
orders, and it does not trade while the app is closed.

Open it from the project folder by double-clicking `Launch-Paper-Trading.bat`,
or from PowerShell:

```powershell
.\.venv\Scripts\python.exe -m streamlit run app\competition_dashboard.py
```

The launcher uses this project's `.venv` and resolves paths from its own
folder.

Saved cash, holdings, closed trades, and every generated plan live in
`paper_trading_data\paper_portfolio.sqlite3`. Generating a plan adds a new
version; it does not erase older plans. Restarting the app reloads the latest
plan. A recommendation is not a trade until you record it. Recording uses the
same paper-trading checks as a manual open or close, then marks that plan as
needing a new generation.

Back up by copying the `paper_trading_data` folder somewhere else while the
app is closed. Do not commit that folder. Starting the app does not delete it.

Run the regression tests:

```powershell
python -m unittest discover -s tests -v
```

## Strategies

`Baseline` is the original three-factor strategy. Its indicator and signal logic
has not been changed. The registry lets the UI and engine discover strategies
without strategy-specific conditionals.

To add a strategy later:

1. Add a module under `strategies/` with `analyze(ticker, data)` and
   `rank_key(result)` functions.
2. Return `Ticker`, `Score`, `Signal`, `Reason`, and `Factor Details` from
   `analyze`.
3. Add one `StrategyDefinition` entry to `strategies/registry.py`.

Do not pass current-day closing data into a strategy before a trade. The engine
enforces this by passing only rows strictly before each rebalance date.

## Historical data and caching

Market data is stored as one Parquet file per ticker under `data_cache/`.
Repeated and overlapping requests reuse cached ranges. Range-specific yfinance
failures are stored in `data_cache/data_failures.json`, preventing repeated
requests for known-unavailable ranges without blacklisting other dates.

If yfinance no longer serves a historical ticker, a manually supplied file such
as `historical_data/EA.parquet` can act as the secondary provider. See
`historical_data/README.md` for its expected columns.

## Known limitations

- Historical Nasdaq-100 membership is reconstructed from dated public changes.
  It is approximate and is not a licensed daily Nasdaq constituent feed.
- Missing prices for valid historical constituents are reported and those
  tickers are skipped, so affected results have incomplete coverage.
- The historical snapshots currently begin in December 2021.
- Transaction costs are modeled as a simple configurable percentage.
