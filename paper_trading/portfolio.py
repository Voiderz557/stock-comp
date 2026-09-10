"""Pure P&L math and candidate scanning for the paper-trading dashboard.

Nothing in this module performs I/O: it takes plain data in (account
state, open-position rows, price data, strategy definitions) and returns
plain data out. This keeps it fully unit-testable without a database,
network access, or Streamlit.
"""

from strategies.registry import invoke_analyze


def latest_close_prices(price_data_by_ticker):
    """Extract the most recent available Close price for each ticker.

    `price_data_by_ticker` maps ticker -> a price DataFrame with a "Close"
    column (as returned by `data.market_data.load_market_data`). Tickers
    with no usable rows are simply omitted from the result.
    """
    prices = {}
    for ticker, frame in (price_data_by_ticker or {}).items():
        if frame is None or frame.empty or "Close" not in frame.columns:
            continue
        closes = frame["Close"].dropna()
        if closes.empty:
            continue
        prices[ticker] = float(closes.iloc[-1])
    return prices


def compute_position_metrics(position, current_price):
    """Return `position` enriched with current price, value, and P&L fields.

    `position` is a dict with at least: ticker, direction, entry_price,
    quantity, allocated_capital (as produced by `paper_trading.storage`).
    """
    entry_price = position["entry_price"]
    quantity = position["quantity"]
    allocated_capital = position["allocated_capital"]
    direction = position["direction"]

    if direction == "LONG":
        unrealized_pnl = (current_price - entry_price) * quantity
        market_value = current_price * quantity
    else:
        unrealized_pnl = (entry_price - current_price) * quantity
        market_value = allocated_capital + unrealized_pnl

    unrealized_pnl_percent = (
        unrealized_pnl / allocated_capital if allocated_capital else 0.0
    )

    enriched = dict(position)
    enriched.update(
        {
            "Current Price": current_price,
            "Market Value": market_value,
            "Unrealized P&L": unrealized_pnl,
            "Unrealized P&L %": unrealized_pnl_percent,
        }
    )
    return enriched


def summarize_portfolio(account_state, open_positions, current_prices):
    """Roll open positions + account cash up into one portfolio summary.

    If a ticker in `open_positions` has no entry in `current_prices`, its
    entry price is used as a conservative stand-in and the row is flagged
    with `"Price Unavailable": True` so callers can surface that clearly.
    """
    position_rows = []
    total_market_value = 0.0
    total_unrealized_pnl = 0.0

    for position in open_positions:
        ticker = position["ticker"]
        current_price = current_prices.get(ticker)
        price_unavailable = current_price is None
        if price_unavailable:
            current_price = position["entry_price"]

        metrics = compute_position_metrics(position, current_price)
        metrics["Price Unavailable"] = price_unavailable
        position_rows.append(metrics)
        total_market_value += metrics["Market Value"]
        total_unrealized_pnl += metrics["Unrealized P&L"]

    cash = account_state["Cash"]
    realized_pnl = account_state["Realized P&L"]
    starting_capital = account_state["Starting Capital"]
    portfolio_value = cash + total_market_value

    return {
        "Cash": cash,
        "Realized P&L": realized_pnl,
        "Starting Capital": starting_capital,
        "Portfolio Value": portfolio_value,
        "Total Market Value": total_market_value,
        "Total Unrealized P&L": total_unrealized_pnl,
        "Total P&L": realized_pnl + total_unrealized_pnl,
        "Open Positions": position_rows,
    }


def scan_for_candidates(universe, strategy_names, price_data_by_ticker, get_strategy_fn, min_price=0.0):
    """Run every strategy's `analyze()` against every ticker in `universe`.

    Returns `{strategy_name: [BUY-signal result dicts, ranked strongest
    first via that strategy's own rank_key]}`. Tickers with missing/empty
    price history, or whose `analyze()` raises or returns None, are simply
    skipped - this never modifies strategy logic, it only calls the public
    `analyze`/`rank_key` interface every strategy already exposes.
    """
    candidates_by_strategy = {}
    for strategy_name in strategy_names:
        definition = get_strategy_fn(strategy_name)
        rows = []
        for ticker in universe:
            data = (price_data_by_ticker or {}).get(ticker)
            if data is None or data.empty:
                continue
            try:
                preferred = getattr(definition, "benchmark_ticker", None)
                relative_benchmark = (price_data_by_ticker or {}).get(preferred) if preferred else None
                result = invoke_analyze(
                    definition.analyze,
                    ticker,
                    data,
                    benchmark_data=relative_benchmark,
                )
            except Exception:
                continue
            if not result or result.get("Signal") != "BUY":
                continue
            price = result.get("Price")
            if min_price and price is not None and price < min_price:
                continue
            rows.append(result)
        rows.sort(key=definition.rank_key, reverse=True)
        candidates_by_strategy[strategy_name] = rows
    return candidates_by_strategy
