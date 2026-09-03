import pandas as pd
import yfinance as yf

from config import (
    BACKTEST_BENCHMARK,
    BACKTEST_END_DATE,
    BACKTEST_FEE_RATE,
    BACKTEST_MAX_POSITIONS,
    BACKTEST_START_DATE,
    BACKTEST_STARTING_CASH,
    LONG_MOMENTUM_DAYS,
    MOVING_AVERAGE_DAYS,
    STOCK_UNIVERSE,
)
from scanner import analyze_stock


def download_backtest_data():
    """Download the universe, benchmark, and indicator warm-up history."""
    start_date = pd.Timestamp(BACKTEST_START_DATE)
    end_date = pd.Timestamp(BACKTEST_END_DATE)

    if start_date >= end_date:
        raise ValueError("Backtest start date must be before the end date.")

    required_days = max(
        LONG_MOMENTUM_DAYS + 1,
        MOVING_AVERAGE_DAYS,
    )
    warmup_start = start_date - pd.Timedelta(days=required_days * 3)

    tickers = list(STOCK_UNIVERSE)
    if BACKTEST_BENCHMARK not in tickers:
        tickers.append(BACKTEST_BENCHMARK)

    print(f"Downloading historical data for {len(tickers)} symbols...")

    return yf.download(
        tickers=tickers,
        start=warmup_start.strftime("%Y-%m-%d"),
        # yfinance treats end as exclusive, so include one extra calendar day.
        end=(end_date + pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
        group_by="ticker",
        auto_adjust=True,
        threads=True,
        progress=False,
    )


def get_ticker_data(downloaded_data, ticker):
    """Extract one ticker's rows from the batch download."""
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


def rank_buy_candidates(downloaded_data, rebalance_date):
    """Rank BUY stocks using information known before the trading day."""
    candidates = []

    for ticker in STOCK_UNIVERSE:
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

    return candidates[:BACKTEST_MAX_POSITIONS]


def rebalance_portfolio(
    downloaded_data,
    rebalance_date,
    selected_stocks,
    cash,
    holdings,
    trades,
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
            fee = proceeds * BACKTEST_FEE_RATE
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

        maximum_affordable = cash / (price * (1 + BACKTEST_FEE_RATE))
        shares_to_buy = min(shares_to_buy, maximum_affordable)

        if shares_to_buy <= 0.000001:
            continue

        cost = shares_to_buy * price
        fee = cost * BACKTEST_FEE_RATE
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


def run_backtest():
    """Run the weekly portfolio simulation and return its records."""
    downloaded_data = download_backtest_data()
    benchmark_data = get_ticker_data(downloaded_data, BACKTEST_BENCHMARK)

    if benchmark_data is None:
        raise ValueError("Benchmark data could not be downloaded.")

    start_date = pd.Timestamp(BACKTEST_START_DATE)
    end_date = pd.Timestamp(BACKTEST_END_DATE)
    simulation_dates = benchmark_data.loc[
        (benchmark_data.index >= start_date)
        & (benchmark_data.index <= end_date)
    ].index

    if simulation_dates.empty:
        raise ValueError("No trading dates exist in the configured range.")

    cash = BACKTEST_STARTING_CASH
    holdings = {}
    trades = []
    portfolio_history = []
    previous_week = None

    for date in simulation_dates:
        week = (date.isocalendar().year, date.isocalendar().week)

        if week != previous_week:
            selected_stocks = rank_buy_candidates(downloaded_data, date)
            cash = rebalance_portfolio(
                downloaded_data,
                date,
                selected_stocks,
                cash,
                holdings,
                trades,
            )
            previous_week = week

        portfolio_value = calculate_portfolio_value(
            downloaded_data,
            date,
            cash,
            holdings,
        )
        portfolio_history.append(
            {
                "Date": date,
                "Portfolio Value": portfolio_value,
                "Cash": cash,
                "Holdings": holdings.copy(),
            }
        )

    first_date = simulation_dates[0]
    last_date = simulation_dates[-1]
    benchmark_start = get_trade_price(benchmark_data, first_date)
    benchmark_end = get_valuation_price(benchmark_data, last_date)
    benchmark_return = benchmark_end / benchmark_start - 1

    ending_value = portfolio_history[-1]["Portfolio Value"]
    total_return = ending_value / BACKTEST_STARTING_CASH - 1

    return {
        "Starting Value": BACKTEST_STARTING_CASH,
        "Ending Value": ending_value,
        "Total Return": total_return,
        "Benchmark Return": benchmark_return,
        "Portfolio History": portfolio_history,
        "Holdings": holdings,
        "Trades": trades,
    }


def display_backtest_results(results):
    """Print the required Version 1 summary."""
    print()
    print("BACKTEST VERSION 1")
    print(f"Period: {BACKTEST_START_DATE} to {BACKTEST_END_DATE}")
    print(f"Starting value: ${results['Starting Value']:,.2f}")
    print(f"Ending value:   ${results['Ending Value']:,.2f}")
    print(f"Total return:   {results['Total Return']:+.2%}")
    print(
        f"{BACKTEST_BENCHMARK} return: "
        f"{results['Benchmark Return']:+.2%}"
    )
    print(f"Number of trades: {len(results['Trades'])}")
    print()
    print("Important limitation: this uses today's test universe, which creates")
    print("survivorship bias when testing historical periods.")


if __name__ == "__main__":
    backtest_results = run_backtest()
    display_backtest_results(backtest_results)
