import yfinance as yf
from config import DATA_PERIOD, MOVING_AVERAGE_DAYS
from config import DATA_PERIOD
from strat import (
    calculate_momentum,
    calculate_moving_average,
    generate_signal,
)


def analyze_stock(ticker):
    data = yf.Ticker(ticker).history(period=DATA_PERIOD)

    if data.empty:
        return None

    try:
        short_momentum, long_momentum = calculate_momentum(data)
        current_price, moving_average = calculate_moving_average(
    data,
    MOVING_AVERAGE_DAYS,
)
        signal, score = generate_signal(data)
    except ValueError:
        return None

    result = {
        "Ticker": ticker,
        "Price": current_price,
        "Short Momentum": short_momentum,
        "Long Momentum": long_momentum,
        "Moving Average": moving_average,
        "Signal": signal,
        "Score": score,
    }

    return result