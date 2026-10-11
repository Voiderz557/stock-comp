# Historical data gap report

Status: research blocked; no valid leaderboard or promotion decision.

## Coverage state

The isolated recovery at
`benchmark_exports/recovery-20260925T005016703146Z/` recovered 83 affected
histories. A fresh read-only audit still fails for ANSS, ATVI, EA, SGEN, SPLK,
and WBA. These securities remain in the point-in-time universe.

The recovery's recorded production-cache hashes no longer match `AAPL.parquet`
and `coverage.json` because the live cache subsequently added the 2026-09-25
AAPL session. The historical 2023–2025 gap result is unchanged, but the frozen
candidate must not be promoted wholesale over the newer production cache.

## Provider checks

- Yahoo's chart endpoint returned `No data found, symbol may be delisted` for
  ANSS, ATVI, SGEN, SPLK, and WBA. EA returned that the requested 2023–2025
  range does not exist. Existing range-specific failure records are retained.
- Nasdaq's public historical quote endpoint returned no records for the six
  symbols for the requested historical range.
- Stooq returned a JavaScript proof-of-work page rather than CSV. The challenge
  was not bypassed.
- No paid API, trial, account, credential, or paid fallback was configured.

The project now includes `python -m benchmarking.import_prices`, which validates
and stages an attributable user-supplied CSV/Parquet outside production. It
requires complete SPY-calendar sessions, valid OHLCV, explicit raw/adjusted
declaration, a source URL without credential-bearing query parameters, and
checksums. Passing this validation does not independently prove the declared
adjustment convention and does not promote the file.

## Verified terminal events

- ATVI: Microsoft completed the acquisition on 2023-10-13 for $95 cash per
  share. Source: https://www.sec.gov/Archives/edgar/data/718877/000110465923108985/tm2328253d1_8k.htm
- SGEN: Pfizer completed the acquisition on 2023-12-14 for $229 cash per share.
  Source: https://www.pfizer.com/news/press-release/press-release-detail/pfizer-completes-acquisition-seagen
- SPLK: Cisco completed the acquisition on 2024-03-18 for $157 cash per share.
  Source: https://www.sec.gov/Archives/edgar/data/858877/000119312524070175/d783088dex991.htm
- ANSS: Synopsys completed the acquisition on 2025-07-17. Final consideration
  was $199.91 cash plus 0.3399 SNPS shares per ANSS share. Source:
  https://www.sec.gov/Archives/edgar/data/883241/000114036125026139/ef20051970_8k.htm
- WBA: the Sycamore acquisition completed on 2025-08-28. Consideration was
  $11.45 cash plus a non-transferable right to receive up to $3.00 from future
  VillageMD-related proceeds. Source:
  https://www.sec.gov/Archives/edgar/data/1618921/000119312525190603/d87240dex991.htm
- EA: the PIF/Silver Lake/Affinity acquisition completed on 2026-08-04 for $210
  cash per share; Nasdaq trading was suspended before the 2026-08-05 open.
  Source: https://www.sec.gov/Archives/edgar/data/712515/000114036126031415/xslSCHEDULE_13D_X02/primary_doc.xml

The benchmark still does not model cash/stock/contingent terminal consideration.
It now fails a method closed if a simulated position remains held after a known
last public date, instead of treating a stale last close as a valid continuing
mark. This check does not change the shared backtesting engine.

## Data acquisition options requiring user choice

The following providers document delisted-history coverage but require an account,
subscription, trial, or API token. They were not activated:

- EODHD delisted histories and end-of-day API:
  https://eodhd.com/financial-apis/delisted-stock-companies-data-2
- Norgate US delisted securities and historical constituents:
  https://norgatedata.com/data-content-tables.php
- Polygon daily aggregate flat files:
  https://polygon.io/docs/flat-files/stocks/day-aggregates/2016/01
- Nasdaq U.S. Equity Tick History:
  https://www.nasdaq.com/products/data/equities/us-equity-tick-history

The minimum next input is a trustworthy daily OHLCV export for the six symbols,
covering 2020-04-29 through each last public date (or through 2025-12-31 for EA's
competition-period requirement), with documented split/dividend adjustment rules.
Raw data is acceptable if all six symbols and SPY/QQQ are transformed consistently
in a separate candidate.
