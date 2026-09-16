"""Pure P&L math and candidate scanning for the paper-trading dashboard.

Nothing in this module performs I/O: it takes plain data in (account
state, open-position rows, price data, strategy definitions) and returns
plain data out. This keeps it fully unit-testable without a database,
network access, or Streamlit.
"""

import math

from strategies.registry import invoke_analyze


def is_valid_market_price(price):
    """True when `price` is a finite number strictly greater than zero."""
    if price is None:
        return False
    try:
        price = float(price)
    except (TypeError, ValueError):
        return False
    return math.isfinite(price) and price > 0


def position_notional_exposure(quantity, price):
    """Mark-to-market notional used for shared LONG+SHORT gross exposure.

    LONG current market value = quantity * current_price
    SHORT absolute current market value = abs(quantity * current_price)

    Quantity is stored positive for both directions, so abs(quantity * price)
    is the contribution in either case. This is NOT the short's P&L "Market
    Value" (allocated_capital + unrealized P&L).
    """
    return abs(float(quantity) * float(price))


def compute_exposure_summary(open_positions, current_prices, starting_capital):
    """Mark existing positions to market for the shared capacity ceiling.

    Gross Exposure = Current Long Exposure + Current Short Exposure
    Remaining Capacity = max(0, Starting Capital - Gross Exposure)

    Missing or invalid current prices are NEVER replaced with entry price.
    Raises ValueError so a new open cannot silently under-count exposure.
    """
    if starting_capital is None:
        raise ValueError(
            "Starting capital is unknown, so shared gross-exposure capacity "
            "cannot be enforced. Existing starting capital is never inferred "
            "from current cash."
        )
    try:
        starting_capital = float(starting_capital)
    except (TypeError, ValueError) as error:
        raise ValueError(
            "Starting capital is unknown, so shared gross-exposure capacity "
            "cannot be enforced. Existing starting capital is never inferred "
            "from current cash."
        ) from error
    if not math.isfinite(starting_capital) or starting_capital <= 0:
        raise ValueError(
            "Starting capital is unknown, so shared gross-exposure capacity "
            "cannot be enforced. Existing starting capital is never inferred "
            "from current cash."
        )

    prices = current_prices or {}
    missing = []
    invalid = []
    long_exposure = 0.0
    short_exposure = 0.0

    for position in open_positions:
        ticker = position["ticker"]
        raw_price = prices.get(ticker)
        if raw_price is None:
            missing.append(ticker)
            continue
        if not is_valid_market_price(raw_price):
            invalid.append(ticker)
            continue
        exposure = position_notional_exposure(position["quantity"], raw_price)
        if position["direction"] == "LONG":
            long_exposure += exposure
        else:
            short_exposure += abs(exposure)

    if missing or invalid:
        parts = []
        if missing:
            parts.append(
                "missing current prices for " + ", ".join(sorted(set(missing)))
            )
        if invalid:
            parts.append(
                "invalid current prices for " + ", ".join(sorted(set(invalid)))
            )
        raise ValueError(
            "Cannot compute shared gross-exposure capacity because "
            + " and ".join(parts)
            + ". Capacity is marked to market and will not fall back to entry price."
        )

    gross_exposure = long_exposure + short_exposure
    remaining_capacity = max(0.0, starting_capital - gross_exposure)
    return {
        "Starting Capital": starting_capital,
        "Current Long Exposure": long_exposure,
        "Current Short Exposure": short_exposure,
        "Gross Exposure": gross_exposure,
        "Remaining Capacity": remaining_capacity,
    }


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
    current_exposure = None
    if is_valid_market_price(current_price):
        current_exposure = position_notional_exposure(quantity, current_price)

    enriched = dict(position)
    enriched.update(
        {
            "Current Price": current_price,
            "Market Value": market_value,
            "Current Exposure": current_exposure,
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
        if price_unavailable:
            metrics["Current Exposure"] = None
        position_rows.append(metrics)
        total_market_value += metrics["Market Value"]
        total_unrealized_pnl += metrics["Unrealized P&L"]

    cash = account_state["Cash"]
    realized_pnl = account_state["Realized P&L"]
    starting_capital = account_state["Starting Capital"]
    portfolio_value = cash + total_market_value

    exposure = {
        "Current Long Exposure": None,
        "Current Short Exposure": None,
        "Gross Exposure": None,
        "Remaining Capacity": None,
        "Capacity Available": False,
        "Capacity Error": None,
    }
    try:
        exposure_summary = compute_exposure_summary(
            open_positions, current_prices, starting_capital
        )
        exposure.update(
            {
                "Current Long Exposure": exposure_summary["Current Long Exposure"],
                "Current Short Exposure": exposure_summary["Current Short Exposure"],
                "Gross Exposure": exposure_summary["Gross Exposure"],
                "Remaining Capacity": exposure_summary["Remaining Capacity"],
                "Capacity Available": True,
                "Capacity Error": None,
            }
        )
    except ValueError as error:
        exposure["Capacity Error"] = str(error)

    return {
        "Cash": cash,
        "Realized P&L": realized_pnl,
        "Starting Capital": starting_capital,
        "Portfolio Value": portfolio_value,
        "Total Market Value": total_market_value,
        "Total Unrealized P&L": total_unrealized_pnl,
        "Total P&L": realized_pnl + total_unrealized_pnl,
        "Open Positions": position_rows,
        **exposure,
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
