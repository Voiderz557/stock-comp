"""Pure recommendation/allocation logic for the paper-trading dashboard.

This module has NO Streamlit dependency and sends no orders anywhere. It only
combines already-computed strategy results into a combined table, decides a
Recommended Action, and works out a recommendation-only paper allocation.
Separating this from `app/competition_dashboard.py` keeps it directly
testable and keeps UI code out of the decision logic (matching the existing
project convention of keeping strategy/backtest logic separate from
display).

SAFETY: nothing here executes trades. `allocate_longs`/`allocate_shorts`/
`build_paper_portfolio` describe a hypothetical "if I opened these today"
allocation for display only; actually persisting an opened position is done
by the dashboard calling `paper_trading.storage.PaperPortfolioStore`
directly, only when the user clicks to do so.
"""

from config import (
    MAX_POSITION_VALUE,
    MAX_SHORT_POSITION_VALUE,
    MIN_STOCK_PRICE,
)


LONG = "LONG"
SHORT = "SHORT"
WATCH = "WATCH"


# ---------------------------------------------------------------------------
# Per-strategy "how bullish/bearish is this evidence" contributions.
# ---------------------------------------------------------------------------
def baseline_long_contribution(baseline_result):
    """Baseline only ever supports LONG evidence, never SHORT.

    BUY -> a positive contribution (Baseline's 0-3 score normalized to 0..1).
    WAIT / AVOID -> neutral (0). AVOID is explicitly "neutral/bearish
    information only" - it is never turned into a SHORT signal here.
    """
    if baseline_result is None:
        return 0.0
    if baseline_result["Signal"] == "BUY":
        return baseline_result["Score"] / 3.0
    return 0.0


def momentum_v2_long_contribution(momentum_v2_result):
    """Momentum V2 is long-only; only its positive score counts as support."""
    if momentum_v2_result is None:
        return 0.0
    return max(momentum_v2_result["Score"], 0.0)


def aggressive_long_contribution(aggressive_result):
    """Only the positive part of Aggressive Momentum V1's score is bullish."""
    if aggressive_result is None:
        return 0.0
    return max(aggressive_result["Score"], 0.0)


def aggressive_short_contribution(aggressive_result):
    """Only the negative part of Aggressive Momentum V1's score is bearish.

    This is the ONLY source of short evidence in the whole dashboard - see
    `combined_short_score`.
    """
    if aggressive_result is None:
        return 0.0
    return max(-aggressive_result["Score"], 0.0)


def combined_long_score(baseline_result, momentum_v2_result, aggressive_result):
    """Simple sum of independent bullish evidence from every strategy.

    This is a naive Version-1 heuristic (not a normalized/weighted blend
    across strategies) - good enough to rank candidates, not a scientifically
    calibrated combined score.
    """
    return (
        baseline_long_contribution(baseline_result)
        + momentum_v2_long_contribution(momentum_v2_result)
        + aggressive_long_contribution(aggressive_result)
    )


def combined_short_score(aggressive_result):
    """Bearish evidence comes ONLY from Aggressive Momentum V1.

    Baseline's AVOID is explicitly excluded - Baseline is never rewritten to
    support SHORT, and an AVOID alone must never generate a SHORT
    recommendation.
    """
    return aggressive_short_contribution(aggressive_result)


def recommend_action(baseline_result, momentum_v2_result, aggressive_result):
    """LONG / SHORT / WATCH for one ticker from its strategy results."""
    aggressive_signal = aggressive_result["Signal"] if aggressive_result else None
    baseline_signal = baseline_result["Signal"] if baseline_result else None
    momentum_v2_signal = momentum_v2_result["Signal"] if momentum_v2_result else None

    # SHORT requires explicit bearish evidence from Aggressive Momentum V1 -
    # never purely because Baseline says AVOID. BUY is the strategy's
    # bullish signal (LONG is still accepted for compatibility).
    if aggressive_signal == SHORT:
        return SHORT

    has_long_signal = (
        baseline_signal == "BUY"
        or momentum_v2_signal == "BUY"
        or aggressive_signal in (LONG, "BUY")
    )
    if has_long_signal and combined_long_score(
        baseline_result, momentum_v2_result, aggressive_result
    ) > 0:
        return LONG

    return WATCH


def build_reason(action, baseline_result, momentum_v2_result, aggressive_result):
    """Human-readable explanation for one Recommended Action."""
    contributors = []
    if baseline_result and baseline_result["Signal"] == "BUY":
        contributors.append("Baseline BUY")
    if momentum_v2_result and momentum_v2_result["Signal"] == "BUY":
        contributors.append("Momentum V2 BUY")
    if aggressive_result and aggressive_result["Signal"] in (LONG, SHORT, "BUY"):
        contributors.append(f"Aggressive Momentum V1 {aggressive_result['Signal']}")

    if action == SHORT:
        if aggressive_result is not None:
            return f"SHORT: {aggressive_result['Reason']}"
        return "SHORT: bearish evidence from Aggressive Momentum V1."
    if action == LONG:
        if contributors:
            return f"LONG: {' and '.join(contributors)}."
        return "LONG: combined long evidence is positive."
    return "WATCH: no strategy currently shows enough conviction for a LONG or SHORT."


def build_recommendation_row(
    ticker, current_price, baseline_result, momentum_v2_result, aggressive_result
):
    """One row of the combined scanner table for a single ticker."""
    action = recommend_action(baseline_result, momentum_v2_result, aggressive_result)
    long_score = combined_long_score(
        baseline_result, momentum_v2_result, aggressive_result
    )
    short_score = combined_short_score(aggressive_result)
    reason = build_reason(action, baseline_result, momentum_v2_result, aggressive_result)

    return {
        "Ticker": ticker,
        "Current Price": current_price,
        "Baseline Signal": baseline_result["Signal"] if baseline_result else None,
        "Baseline Score": baseline_result["Score"] if baseline_result else None,
        "Momentum V2 Signal": momentum_v2_result["Signal"] if momentum_v2_result else None,
        "Momentum V2 Score": momentum_v2_result["Score"] if momentum_v2_result else None,
        "Aggressive Signal": aggressive_result["Signal"] if aggressive_result else None,
        "Aggressive Score": aggressive_result["Score"] if aggressive_result else None,
        "Combined Long Score": long_score,
        "Combined Short Score": short_score,
        "Recommended Action": action,
        "Reason": reason,
    }


# ---------------------------------------------------------------------------
# Recommendation-only allocation (no orders sent, no persistence here).
#
# LONG and SHORT are NOT two separate wallets - they draw from ONE shared
# capacity pool (`starting_capital`). Opening SHORT exposure permanently
# uses up part of the SAME capacity that a LONG would otherwise have been
# able to use - see paper_trading/storage.py for the equivalent enforcement
# on the persisted account.
# ---------------------------------------------------------------------------
def allocate_longs(candidates, available_capacity, max_position_value=MAX_POSITION_VALUE):
    """Allocate up to `max_position_value` per LONG candidate, strongest
    first, until `available_capacity` runs out.

    `candidates` must already be sorted strongest-first. Returns
    (allocations, capacity_remaining). If there are not enough strong
    candidates, the remaining capacity is simply left unused.
    """
    remaining_capacity = float(available_capacity)
    allocations = []
    for candidate in candidates:
        if remaining_capacity <= 0:
            break
        allocation = min(max_position_value, remaining_capacity)
        if allocation <= 0:
            continue
        allocations.append(
            {
                "Ticker": candidate["Ticker"],
                "Allocation": allocation,
                "Score": candidate["Combined Long Score"],
                "Reason": candidate["Reason"],
            }
        )
        remaining_capacity -= allocation
    return allocations, remaining_capacity


def allocate_shorts(
    candidates,
    allow_shorts,
    available_capacity,
    max_position_value=MAX_SHORT_POSITION_VALUE,
):
    """Allocate short exposure from the SAME shared `available_capacity`
    pool already drawn down by `allocate_longs` (the caller is responsible
    for passing in whatever capacity is left after longs, NOT an
    independent short-only budget). Never forces a short: if there are no
    sufficiently bearish candidates (or shorting is disabled), exposure is
    $0 and the capacity is left untouched for the caller.

    `candidates` must already be sorted strongest-bearish-first. Returns
    (allocations, capacity_remaining).
    """
    if not allow_shorts:
        return [], float(available_capacity)

    remaining_capacity = float(available_capacity)
    allocations = []
    for candidate in candidates:
        if remaining_capacity <= 0:
            break
        exposure = min(max_position_value, remaining_capacity)
        if exposure <= 0:
            continue
        allocations.append(
            {
                "Ticker": candidate["Ticker"],
                "Exposure": exposure,
                "Score": candidate["Combined Short Score"],
                "Reason": candidate["Reason"],
            }
        )
        remaining_capacity -= exposure
    return allocations, remaining_capacity


def build_paper_portfolio(
    rows,
    starting_capital,
    allow_shorts,
    max_long_position_value=MAX_POSITION_VALUE,
    max_short_position_value=MAX_SHORT_POSITION_VALUE,
    min_stock_price=MIN_STOCK_PRICE,
):
    """Build the full recommended paper portfolio from combined-table rows.

    LONG and SHORT share ONE capacity pool, `starting_capital` - shorts are
    NOT a separate extra wallet on top of it. LONGs are allocated first
    (strongest first) and SHORTs draw from whatever capacity is left over
    (also strongest first), so:

        Gross Exposure = Gross Long Exposure + Gross Short Exposure
                       <= starting_capital

    always holds for the allocations this function produces. No
    margin/leverage beyond that shared cap.
    """
    eligible_rows = [
        row for row in rows if row["Current Price"] is not None
        and row["Current Price"] >= min_stock_price
    ]

    long_candidates = sorted(
        (row for row in eligible_rows if row["Recommended Action"] == LONG),
        key=lambda row: row["Combined Long Score"],
        reverse=True,
    )
    short_candidates = sorted(
        (row for row in eligible_rows if row["Recommended Action"] == SHORT),
        key=lambda row: row["Combined Short Score"],
        reverse=True,
    )

    long_allocations, capacity_after_longs = allocate_longs(
        long_candidates, starting_capital, max_long_position_value
    )
    short_allocations, capacity_remaining = allocate_shorts(
        short_candidates,
        allow_shorts,
        capacity_after_longs,
        max_short_position_value,
    )

    gross_long_exposure = sum(item["Allocation"] for item in long_allocations)
    gross_short_exposure = sum(item["Exposure"] for item in short_allocations)
    gross_exposure = gross_long_exposure + gross_short_exposure
    net_exposure = gross_long_exposure - gross_short_exposure

    return {
        "Longs": long_allocations,
        "Shorts": short_allocations,
        "Remaining Capacity": capacity_remaining,
        "Gross Long Exposure": gross_long_exposure,
        "Gross Short Exposure": gross_short_exposure,
        "Gross Exposure": gross_exposure,
        "Net Exposure": net_exposure,
    }
