from config import (
    LONG_MOMENTUM_DAYS,
    MOVING_AVERAGE_DAYS,
    SHORT_MOMENTUM_DAYS,
)

def calculate_momentum(data):
    closing_prices = data["Close"]
    required_days = LONG_MOMENTUM_DAYS + 1

    if len(data) < required_days:
        raise ValueError("Not enough historical data.")

    current_price = closing_prices.iloc[-1]

    short_old_price = closing_prices.iloc[-(SHORT_MOMENTUM_DAYS + 1)]
    long_old_price = closing_prices.iloc[-(LONG_MOMENTUM_DAYS + 1)]

    short_momentum = (current_price - short_old_price) / short_old_price
    long_momentum = (current_price - long_old_price) / long_old_price

    return short_momentum, long_momentum

def calculate_moving_average(data, days):
    closing_prices = data["Close"]
    current_price = closing_prices.iloc[-1]
    moving_average = closing_prices.tail(days).mean()

    return current_price, moving_average

def generate_signal(data):
    momentum_5d, momentum_20d = calculate_momentum(data)
    current_price, moving_average = calculate_moving_average(
        data,
        MOVING_AVERAGE_DAYS,
    )

    score = 0

    if momentum_5d > 0:
        score += 1

    if momentum_20d > 0:
        score += 1

    if current_price > moving_average:
        score += 1

    if score == 3:
        signal = "BUY"
    elif score == 0:
        signal = "AVOID"
    else:
        signal = "WAIT"

    return signal, score
