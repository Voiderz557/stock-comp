"""Controlled Breakout V1: preregistered risk controls around Breakout Volume V1.

The underlying formula and BUY criteria belong entirely to Breakout Volume V1.
This research candidate adds exactly two rejection guards and one ranking
transform.  The constants below were preregistered before validation and must
not be tuned from the resulting backtests.
"""

from copy import deepcopy

from . import breakout_volume_v1


MAX_DAILY_VOLATILITY_20D = 0.035
MAX_MA50_EXTENSION = 0.20
RANKING_VOLATILITY_FLOOR = 0.01

REQUIRED_HISTORY_DAYS = breakout_volume_v1.REQUIRED_HISTORY_DAYS


def analyze(ticker, data):
    """Apply preregistered guards to an unchanged Breakout Volume V1 result."""
    base_result = breakout_volume_v1.analyze(ticker, data)
    if base_result is None:
        return None

    result = deepcopy(base_result)
    details = deepcopy(base_result.get("Factor Details", {}))
    price = float(details["Price"])
    ma50 = float(details["MA50"])
    volatility_20d = float(details["Volatility 20D"])
    ma50_extension = (price / ma50) - 1 if ma50 > 0 else float("inf")
    volatility_guard = volatility_20d >= MAX_DAILY_VOLATILITY_20D
    # Compare prices directly so an exact 20% boundary cannot miss because
    # binary floating-point represents 120/100 - 1 just below 0.20.
    extension_guard = price >= ma50 * (1.0 + MAX_MA50_EXTENSION)

    base_score = float(base_result["Score"])
    ranking_score = base_score / max(volatility_20d, RANKING_VOLATILITY_FLOOR)

    signal = base_result["Signal"]
    reason = base_result["Reason"]
    if signal == "BUY" and (volatility_guard or extension_guard):
        signal = "WAIT"
        rejected_by = []
        if volatility_guard:
            rejected_by.append(
                f"20D daily volatility {volatility_20d:.2%} >= {MAX_DAILY_VOLATILITY_20D:.2%}"
            )
        if extension_guard:
            rejected_by.append(
                f"MA50 extension {ma50_extension:.2%} >= {MAX_MA50_EXTENSION:.2%}"
            )
        reason = f"Controlled breakout rejected the base BUY: {'; '.join(rejected_by)}."

    controlled_details = {
        **details,
        "Base Breakout Score": base_score,
        "MA50 Extension": ma50_extension,
        "Volatility Guard Triggered": volatility_guard,
        "MA50 Extension Guard Triggered": extension_guard,
        "Risk Adjusted Rank Score": ranking_score,
        "Final Score": ranking_score,
    }
    return {
        **result,
        "Score": ranking_score,
        "Signal": signal,
        "Reason": reason,
        "Factor Details": controlled_details,
        **controlled_details,
    }


def rank_key(result):
    """Rank by preregistered score/volatility transform, then breakout strength."""
    return (result["Score"], result["Breakout 20D"])


PARAMETERS = {
    "base_strategy": "Breakout Volume V1",
    "max_daily_volatility_20d": MAX_DAILY_VOLATILITY_20D,
    "max_ma50_extension": MAX_MA50_EXTENSION,
    "ranking_volatility_floor": RANKING_VOLATILITY_FLOOR,
}
