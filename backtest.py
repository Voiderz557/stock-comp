import pandas as pd

from config import (
    BACKTEST_BENCHMARK,
    BACKTEST_END_DATE,
    BACKTEST_FEE_RATE,
    BACKTEST_MAX_POSITIONS,
    BACKTEST_START_DATE,
    BACKTEST_STARTING_CASH,
    LONG_MOMENTUM_DAYS,
    MOVING_AVERAGE_DAYS,
)
from historical_universe import (
    UNIVERSE_SOURCE,
    get_backtest_tickers,
    get_historical_universe,
)
from market_data import load_market_data
from scanner import analyze_stock


def download_backtest_data(
    start_date,
    end_date,
    benchmark,
    status_callback=None,
):
    """Download the universe, benchmark, and indicator warm-up history."""
    required_days = max(
        LONG_MOMENTUM_DAYS + 1,
        MOVING_AVERAGE_DAYS,
    )
    warmup_start = start_date - pd.Timedelta(days=required_days * 3)

    tickers = get_backtest_tickers(start_date, end_date)
    if benchmark not in tickers:
        tickers.append(benchmark)

    try:
        downloaded_data, cache_report = load_market_data(
            tickers,
            warmup_start,
            end_date,
            status_callback=status_callback,
        )
    except Exception as error:
        raise RuntimeError("Market data could not be loaded.") from error

    if not any(not frame.empty for frame in downloaded_data.values()):
        raise RuntimeError("The market data loader returned no results.")

    return downloaded_data, cache_report


def get_ticker_data(downloaded_data, ticker):
    """Extract one ticker's rows from the batch download."""
    if isinstance(downloaded_data, dict):
        ticker_data = downloaded_data.get(ticker)
        if ticker_data is None or ticker_data.empty:
            return None
        return ticker_data.copy()

    available_tickers = downloaded_data.columns.get_level_values(0)

    if ticker not in available_tickers:
        return None

    ticker_data = downloaded_data[ticker].dropna(how="all").copy()

    if ticker_data.empty:
        return None

    return ticker_data


def get_trade_price(ticker_data, date):
    """Return the stock's Open on an exact trading date."""
    if date not in ticker_data.index:
        return None

    price = ticker_data.loc[date, "Open"]

    if pd.isna(price) or price <= 0:
        return None

    return float(price)


def get_valuation_price(ticker_data, date):
    """Return the latest Close known on or before a valuation date."""
    # This <= boundary prevents valuation from reaching into future rows.
    known_closes = ticker_data.loc[
        ticker_data.index <= date,
        "Close",
    ].dropna()

    if known_closes.empty:
        return None

    return float(known_closes.iloc[-1])


def rank_buy_candidates(
    downloaded_data,
    rebalance_date,
    max_positions,
    universe=None,
):
    """Rank BUY stocks using information known before the trading day."""
    candidates = []

    if universe is None:
        universe = get_historical_universe(rebalance_date).tickers

    for ticker in universe:
        ticker_data = get_ticker_data(downloaded_data, ticker)

        if ticker_data is None:
            continue

        if get_trade_price(ticker_data, rebalance_date) is None:
            continue

        # LOOK-AHEAD PROTECTION:
        # The signal receives only rows strictly before the rebalance date.
        # Today's Open is used for trading, but today's Close is never passed
        # to the strategy before that trade occurs.
        historical_data = ticker_data.loc[
            ticker_data.index < rebalance_date
        ].copy()

        result = analyze_stock(ticker, historical_data)

        if result is not None and result["Signal"] == "BUY":
            candidates.append(result)

    candidates.sort(
        key=lambda stock: (
            stock["Score"],
            stock["Long Momentum"],
        ),
        reverse=True,
    )

    return candidates[:max_positions]


def rebalance_portfolio(
    downloaded_data,
    rebalance_date,
    selected_stocks,
    cash,
    holdings,
    trades,
    fee_rate,
):
    """Rebalance selected stocks to equal weights at the day's Open."""
    selected_tickers = [stock["Ticker"] for stock in selected_stocks]
    trade_prices = {}

    for ticker in set(holdings) | set(selected_tickers):
        ticker_data = get_ticker_data(downloaded_data, ticker)

        if ticker_data is None:
            return cash

        price = get_trade_price(ticker_data, rebalance_date)

        if price is None:
            # Keep the existing portfolio unchanged if every needed stock
            # cannot be traded at this Open.
            return cash

        trade_prices[ticker] = price

    portfolio_value = cash + sum(
        shares * trade_prices[ticker]
        for ticker, shares in holdings.items()
    )

    if selected_tickers:
        target_value = portfolio_value / len(selected_tickers)
    else:
        target_value = 0.0

    # Sell removed positions and reduce overweight positions before buying.
    for ticker in list(holdings):
        price = trade_prices[ticker]
        current_shares = holdings[ticker]

        if ticker in selected_tickers:
            target_shares = target_value / price
        else:
            target_shares = 0.0

        shares_to_sell = current_shares - target_shares

        if shares_to_sell > 0.000001:
            proceeds = shares_to_sell * price
            fee = proceeds * fee_rate
            cash += proceeds - fee
            holdings[ticker] = target_shares
            trades.append(
                {
                    "Date": rebalance_date,
                    "Action": "SELL",
                    "Ticker": ticker,
                    "Shares": shares_to_sell,
                    "Price": price,
                    "Fee": fee,
                }
            )

        if holdings.get(ticker, 0.0) <= 0.000001:
            holdings.pop(ticker, None)

    # Buy new positions and increase underweight selected positions.
    for ticker in selected_tickers:
        price = trade_prices[ticker]
        current_shares = holdings.get(ticker, 0.0)
        target_shares = target_value / price
        shares_to_buy = target_shares - current_shares

        if shares_to_buy <= 0.000001:
            continue

        maximum_affordable = cash / (price * (1 + fee_rate))
        shares_to_buy = min(shares_to_buy, maximum_affordable)

        if shares_to_buy <= 0.000001:
            continue

        cost = shares_to_buy * price
        fee = cost * fee_rate
        cash -= cost + fee
        holdings[ticker] = current_shares + shares_to_buy
        trades.append(
            {
                "Date": rebalance_date,
                "Action": "BUY",
                "Ticker": ticker,
                "Shares": shares_to_buy,
                "Price": price,
                "Fee": fee,
            }
        )

    return cash


def calculate_portfolio_value(downloaded_data, date, cash, holdings):
    """Value cash and holdings using closes known by this date."""
    total_value = cash

    for ticker, shares in holdings.items():
        ticker_data = get_ticker_data(downloaded_data, ticker)

        if ticker_data is None:
            continue

        price = get_valuation_price(ticker_data, date)

        if price is not None:
            total_value += shares * price

    return total_value


def run_backtest(
    start_date=BACKTEST_START_DATE,
    end_date=BACKTEST_END_DATE,
    starting_cash=BACKTEST_STARTING_CASH,
    max_positions=BACKTEST_MAX_POSITIONS,
    benchmark=BACKTEST_BENCHMARK,
    fee_rate=BACKTEST_FEE_RATE,
    status_callback=None,
):
    """Run the weekly portfolio simulation and return its records."""
    start_date = pd.Timestamp(start_date)
    end_date = pd.Timestamp(end_date)
    benchmark = benchmark.strip().upper()

    if start_date >= end_date:
        raise ValueError("Start date must be before end date.")
    if starting_cash <= 0:
        raise ValueError("Starting cash must be positive.")
    if max_positions < 1:
        raise ValueError("Maximum positions must be at least 1.")
    if not benchmark:
        raise ValueError("Benchmark ticker cannot be empty.")
    if fee_rate < 0:
        raise ValueError("Transaction fee / slippage cannot be negative.")

    downloaded_data, cache_report = download_backtest_data(
        start_date,
        end_date,
        benchmark,
        status_callback=status_callback,
    )
    benchmark_data = get_ticker_data(downloaded_data, benchmark)

    if benchmark_data is None:
        raise ValueError("Benchmark data could not be downloaded.")

    simulation_dates = benchmark_data.loc[
        (benchmark_data.index >= start_date)
        & (benchmark_data.index <= end_date)
    ].index

    if simulation_dates.empty:
        raise ValueError("No trading dates exist in the configured range.")

    first_date = simulation_dates[0]
    last_date = simulation_dates[-1]
    benchmark_start = get_trade_price(benchmark_data, first_date)

    if benchmark_start is None:
        raise ValueError("Benchmark has no valid opening price on the start date.")

    cash = float(starting_cash)
    holdings = {}
    trades = []
    portfolio_history = []
    previous_week = None

    for date in simulation_dates:
        week = (date.isocalendar().year, date.isocalendar().week)

        if week != previous_week:
            universe_snapshot = get_historical_universe(date)
            selected_stocks = rank_buy_candidates(
                downloaded_data,
                date,
                max_positions,
                universe=universe_snapshot.tickers,
            )
            cash = rebalance_portfolio(
                downloaded_data,
                date,
                selected_stocks,
                cash,
                holdings,
                trades,
                fee_rate,
            )
            previous_week = week

        portfolio_value = calculate_portfolio_value(
            downloaded_data,
            date,
            cash,
            holdings,
        )
        benchmark_price = get_valuation_price(benchmark_data, date)
        benchmark_value = starting_cash * benchmark_price / benchmark_start
        portfolio_history.append(
            {
                "Date": date,
                "Portfolio Value": portfolio_value,
                "Benchmark Value": benchmark_value,
                "Cash": cash,
                "Holdings": holdings.copy(),
            }
        )

    benchmark_end = get_valuation_price(benchmark_data, last_date)
    benchmark_return = benchmark_end / benchmark_start - 1

    ending_value = portfolio_history[-1]["Portfolio Value"]
    total_return = ending_value / starting_cash - 1

    final_holdings = []
    for ticker, shares in holdings.items():
        ticker_data = get_ticker_data(downloaded_data, ticker)
        final_price = get_valuation_price(ticker_data, last_date)
        final_holdings.append(
            {
                "Ticker": ticker,
                "Shares": shares,
                "Final Price": final_price,
                "Market Value": shares * final_price,
            }
        )

    return {
        "Start Date": first_date,
        "End Date": last_date,
        "Benchmark": benchmark,
        "Starting Value": starting_cash,
        "Ending Value": ending_value,
        "Total Return": total_return,
        "Benchmark Return": benchmark_return,
        "Portfolio History": portfolio_history,
        "Holdings": holdings,
        "Final Holdings": final_holdings,
        "Trades": trades,
        "Market Data Cache": cache_report,
        "Universe Approximate": True,
        "Universe Source": UNIVERSE_SOURCE,
        "Universe Snapshot Dates": sorted(
            {
                get_historical_universe(date).effective_date
                for date in simulation_dates
            }
        ),
    }


def display_backtest_results(results):
    """Print the required Version 1 summary."""
    print()
    print("BACKTEST VERSION 1")
    print(
        f"Period: {results['Start Date'].date()} "
        f"to {results['End Date'].date()}"
    )
    print(f"Starting value: ${results['Starting Value']:,.2f}")
    print(f"Ending value:   ${results['Ending Value']:,.2f}")
    print(f"Total return:   {results['Total Return']:+.2%}")
    print(
        f"{results['Benchmark']} return: "
        f"{results['Benchmark Return']:+.2%}"
    )
    print(f"Number of trades: {len(results['Trades'])}")
    print(f"Market data: {results['Market Data Cache']['Status']}")
    print("Historical universe: APPROXIMATE")
    print()
    print("Important limitation: this uses today's test universe, which creates")
    print("survivorship bias when testing historical periods.")
    print(results["Universe Source"])


if __name__ == "__main__":
    backtest_results = run_backtest()
    display_backtest_results(backtest_results)
