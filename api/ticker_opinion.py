"""Read-only ticker research using unchanged registered strategies, no account I/O."""
from datetime import datetime, timezone
import math
import threading

import pandas as pd

from api.external_research import _symbol
from market.regime import compute_market_features, classify_market_regime
from market.strategy_selector import recommend_strategy
from paper_trading.prices import completed_daily_close_frame, latest_daily_close_quote
from strategies.registry import get_strategy, live_strategy_names, invoke_analyze

_gate = threading.Lock()
MAX_QUOTE_AGE_DAYS = 5


class OpinionBusy(Exception):
    pass


def build_ticker_opinion(ticker, *, loader=None, now=None):
    ticker = _symbol(ticker)
    if not ticker:
        raise ValueError("Enter a valid stock ticker, for example AAPL or BRK-B.")
    # Held by the worker until completion, including after an HTTP timeout.
    if not _gate.acquire(blocking=False):
        raise OpinionBusy()
    try:
        return _build(ticker, loader, now or datetime.now(timezone.utc))
    finally:
        _gate.release()


def _build(ticker, loader, now):
    if loader is None:
        from data.market_data import load_market_data
        loader = load_market_data
    definitions = [get_strategy(name) for name in live_strategy_names()]
    symbols = sorted({ticker, "SPY", "QQQ", *(d.benchmark_ticker for d in definitions if d.benchmark_ticker)})
    end = pd.Timestamp(now).tz_localize(None).normalize()
    days = max(400, int(max(d.required_history_days for d in definitions) * 1.6) + 30)
    raw, _ = loader(symbols, end - pd.Timedelta(days=days), end)
    frames = {key: completed_daily_close_frame(frame, now).sort_index() for key, frame in raw.items() if frame is not None}
    quotes = {key: latest_daily_close_quote(frame) for key, frame in frames.items()}
    warnings = []
    quote = quotes.get(ticker)
    reference = max((q["as_of"][:10] for key, q in quotes.items() if key in {"SPY", "QQQ"} and q), default=None)
    def usable(key):
        q = quotes.get(key)
        return bool(q and reference and q["as_of"][:10] == reference and
                    0 <= (end.date() - pd.Timestamp(q["as_of"]).date()).days <= MAX_QUOTE_AGE_DAYS)
    regime = None
    if usable("SPY") and usable("QQQ"):
        try:
            result = classify_market_regime(compute_market_features(frames["SPY"], frames["QQQ"]))
            mapping = recommend_strategy(result.regime)
            regime = {"name": result.regime, "reason": mapping.reason,
                      "preferred_strategies": list(mapping.preferred_strategies)}
        except (ValueError, TypeError, KeyError):
            pass
    if regime is None:
        warnings.append("Market context is unavailable or stale; no purchase eligibility assessment is available.")
    results = []
    for definition in definitions:
        row = {"strategy": definition.name, "signal": "UNAVAILABLE", "score": None,
               "reason": "Insufficient, stale, or mismatched dated history.", "evidence": []}
        frame = frames.get(ticker)
        benchmark = frames.get(definition.benchmark_ticker)
        if usable(ticker) and frame is not None and len(frame) >= definition.required_history_days and (
            not definition.benchmark_ticker or usable(definition.benchmark_ticker)
        ):
            try:
                result = invoke_analyze(definition.analyze, ticker, frame.copy(),
                                        benchmark_data=benchmark.copy() if benchmark is not None else None)
                score = float(result["Score"])
                if result["Signal"] not in {"BUY", "WAIT", "AVOID"} or not math.isfinite(score):
                    raise ValueError("Incomplete signal")
                row.update(signal=result["Signal"], score=score, reason=str(result["Reason"])[:1000])
                details = result.get("Factor Details") or {}
                for window in (5, 20, 60, 120):
                    value = details.get(f"Momentum {window}D")
                    if isinstance(value, (int, float)) and math.isfinite(value):
                        row["evidence"].append(f"Price {'rose' if value >= 0 else 'fell'} {abs(value) * 100:.1f}% over {window} trading days.")
                for window in (50, 200):
                    above = details.get(f"Above MA{window}")
                    if isinstance(above, bool):
                        row["evidence"].append(f"Price is {'above' if above else 'at or below'} its {window}-day average.")
            except Exception:
                row.update(signal="UNAVAILABLE", score=None, reason="This strategy could not produce reliable evidence.", evidence=[])
        results.append(row)
    counts = {signal: sum(r["signal"] == signal for r in results) for signal in ("BUY", "WAIT", "AVOID", "UNAVAILABLE")}
    if counts["UNAVAILABLE"]:
        warnings.append("Some strategies could not be evaluated. Missing evidence is not a bearish signal.")
    if not usable(ticker):
        warnings.append("The stock's latest completed daily price is missing, stale, or differs from the market session.")
    summary = (f"{counts['BUY']} of {len(results)} strategies support buying; {counts['WAIT']} suggest waiting, "
               f"{counts['AVOID']} advise avoiding new purchases, and {counts['UNAVAILABLE']} are unavailable.")
    if regime and not regime["preferred_strategies"]:
        summary += " The broader-market filter currently blocks new purchases."
    return {"ticker": ticker, "as_of": now.isoformat(), "price": quote["price"] if quote else None,
            "price_as_of": quote["as_of"][:10] if quote else None, "summary": summary,
            "strategies": results, "regime": regime, "warnings": warnings,
            "scope": "Rule-based research opinion, not AI or a trade order. Daily closes, not live prices. Scores are strategy-specific, not probabilities. Cash, holdings and competition eligibility have not been checked; use the daily plan for allocations."}
