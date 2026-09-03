import yfinance as yf

from config import (
    DATA_PERIOD,
    LONG_MOMENTUM_DAYS,
    SCANNER_RESULT_LIMIT,
    SHORT_MOMENTUM_DAYS,
    STOCK_UNIVERSE,
)
from strategies.registry import get_strategy


def download_stock_data():
    """Download the whole configured universe in one batch."""
    print(f"Downloading data for {len(STOCK_UNIVERSE)} stocks...")

    try:
        return yf.download(
            tickers=STOCK_UNIVERSE,
            period=DATA_PERIOD,
            group_by="ticker",
            auto_adjust=True,
            threads=True,
            progress=False,
        )
    except Exception as error:
        print(f"Stock download failed: {error}")
        return None


def analyze_stock(ticker, data):
    """Analyze one stock with the configured scanner strategy."""
    return get_strategy("Baseline").analyze(ticker, data)


def scan_stocks():
    """Analyze every configured ticker and rank the valid results."""
    downloaded_data = download_stock_data()

    if downloaded_data is None or downloaded_data.empty:
        return []

    results = []
    available_tickers = downloaded_data.columns.get_level_values(0)

    for ticker in STOCK_UNIVERSE:
        if ticker not in available_tickers:
            print(f"Skipping {ticker}: no downloaded data")
            continue

        ticker_data = downloaded_data[ticker].copy()
        result = analyze_stock(ticker, ticker_data)

        if result is not None:
            results.append(result)
        else:
            print(f"Skipping {ticker}: insufficient or invalid data")

    results.sort(key=get_strategy("Baseline").rank_key, reverse=True)

    return results


def display_results(results):
    """Print the highest-ranked scanner results as a leaderboard."""
    if not results:
        print("No valid stocks were found.")
        return

    displayed_results = results[:SCANNER_RESULT_LIMIT]
    short_label = f"{SHORT_MOMENTUM_DAYS}D MOM"
    long_label = f"{LONG_MOMENTUM_DAYS}D MOM"

    print()
    print(
        f"STOCK LEADERBOARD - showing {len(displayed_results)} "
        f"of {len(results)} valid stocks"
    )

    header = (
        f"{'RANK':<5} | {'TICKER':<6} | {'SCORE':<5} | "
        f"{'SIGNAL':<6} | {'PRICE':>10} | "
        f"{short_label:>9} | {long_label:>9}"
    )

    print(header)
    print("-" * len(header))

    for rank, stock in enumerate(displayed_results, start=1):
        print(
            f"{rank:<5} | "
            f"{stock['Ticker']:<6} | "
            f"{str(stock['Score']) + '/3':<5} | "
            f"{stock['Signal']:<6} | "
            f"${stock['Price']:>9,.2f} | "
            f"{stock['Short Momentum'] * 100:>+8.2f}% | "
            f"{stock['Long Momentum'] * 100:>+8.2f}%"
        )


if __name__ == "__main__":
    ranked_stocks = scan_stocks()
    display_results(ranked_stocks)
