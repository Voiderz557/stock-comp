# Manual historical market data

This directory is the optional secondary market-data provider. Add a file named
`TICKER.parquet` (for example, `EA.parquet`) with a `DatetimeIndex` and these
columns where available:

- `Open`
- `High`
- `Low`
- `Close`
- `Volume`

The loader checks yfinance first. If yfinance returns no rows, it checks this
directory, merges successful fallback data into `data_cache/TICKER.parquet`,
and records `LOCAL PARQUET` in the backtest's data-source report.
