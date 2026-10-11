# Paper-account market watcher (v1)

This is a read-only alert layer for the local competition dashboard. It does
not generate a plan, execute a paper trade, contact a broker, or call an AI
provider. It reuses the saved plan, current paper positions, market-data cache,
and registered strategy analysis. Alert history is stored in
`market_watcher.sqlite3` beside the existing paper portfolio database, not in
the portfolio database. The file is runtime data and is ignored by Git.

## What it checks

- Held tickers and BUY candidates in the latest saved plan. It does **not**
  continuously scan the entire stock universe or monitor every WAIT/AVOID row.
- The latest available daily close and its source date. A close-to-previous-close
  move of at least 5% in either direction is flagged.
- Latest five-day average volume at least 2× the latest 20-day average.
- Changes in the selected strategy's signal for a watched ticker since the
  previous observation, if the same strategy was used for both checks.
- A new saved plan, including newly added/dropped BUY candidates and changed
  holding signals. A dropped BUY candidate is **not** a sell instruction.
- Stale account/plan mismatch, a plan or price session more than four calendar
  days old, missing prices, unavailable signals, provider gaps, and cash or
  gross-capacity restrictions. Missing prices never become entry-price estimates.

Use **Check market changes now** in the dashboard. The optional 15-minute
auto-check works only while that page is open; it is off by default and is not
a background service or push notification. Checks use daily-close data, not
live intraday quotes. An alert is a prompt for human review, not an order.
Repeated checks of the same event are deduplicated in alert history. The
watcher rejects future-dated price rows and abandons a check if the account or
latest plan changes while market data is loading.

## Still outside v1

No AI explanations, external alerts, Quiver, Liquid, real trades, or automatic
plan/trade actions are enabled. Those should be considered separately after
the rule-based alerts have been reviewed against actual data quality.
