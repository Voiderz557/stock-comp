import yfinance as yf
from config import DATA_PERIOD, DEFAULT_TICKER, MOVING_AVERAGE_DAYS

# Import calculations from the strategy file
from strategies.baseline import (
    calculate_momentum,
    calculate_moving_average,
    generate_signal,
)
from scanner.dashboard import show_dashboard


# Ask the user which stock to examine
ticker = input(
    f"Enter a stock ticker (default {DEFAULT_TICKER}): "
).strip().upper()

if ticker == "":
    ticker = DEFAULT_TICKER

# Download historical stock data
stock = yf.Ticker(ticker)
data = stock.history(period=DATA_PERIOD)
if data.empty:
    print("No data found for that ticker.")
    exit()
# Calculate stock indicators
momentum_5d, momentum_20d = calculate_momentum(data)
current_price, moving_average_20 = calculate_moving_average(data, MOVING_AVERAGE_DAYS)
signal, score = generate_signal(data)

# Display the dashboard
show_dashboard(
    ticker,
    data,
    current_price,
    momentum_5d,
    momentum_20d,
    signal,
    score,
)
