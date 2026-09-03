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
