"""Deterministic public-app fixture data.

This module intentionally does not read the user's local SQLite database. A
future cloud repository will resolve records by the verified principal.
"""

from __future__ import annotations

from copy import deepcopy


_DEMO_SNAPSHOT = {
    "snapshot_time": "2026-09-26T13:40:00Z",
    "data_mode": "demo",
    "portfolio": {
        "value": 106842.91,
        "cash": 80503.68,
        "long_exposure": 26339.23,
        "short_exposure": 0.0,
        "unrealized_pnl": 870.26,
    },
    "plan": {
        "generated_at": "2026-09-26T12:36:00Z",
        "market_as_of": "2026-09-26T12:35:00Z",
        "status": "CURRENT",
        "strategy": "Regime-guided strategy selection",
        "summary": "Three candidates need attention while the plan keeps substantial cash available.",
        "warnings": [],
    },
    "regime": {
        "name": "WEAK_UPTREND",
        "label": "Weak uptrend",
        "reason": "Broad indices remain above their long-term trend, but recent momentum is mixed.",
        "trend_strength": 68,
        "market_breadth": 61,
        "volatility": 34,
        "preferred_strategies": ["Momentum V2", "Relative Strength Momentum V1"],
    },
    "recommendations": [
        {
            "ticker": "NVDA",
            "company": "NVIDIA",
            "action": "BUY",
            "score": 88.4,
            "strategy": "Controlled Breakout V1",
            "reason": "Fresh 20-day breakout, supportive volume, and strong relative strength while the broad trend remains constructive.",
            "price": 184.72,
            "move": 2.8,
        },
        {
            "ticker": "META",
            "company": "Meta Platforms",
            "action": "BUY",
            "score": 81.7,
            "strategy": "Relative Strength Momentum V1",
            "reason": "Positive 20- and 60-day momentum with persistent outperformance versus QQQ.",
            "price": 742.18,
            "move": 1.4,
        },
        {
            "ticker": "MSFT",
            "company": "Microsoft",
            "action": "WAIT",
            "score": 68.2,
            "strategy": "Momentum V2",
            "reason": "Trend remains positive, but the stock is extended and volume confirmation is ordinary.",
            "price": 512.41,
            "move": -0.3,
        },
        {
            "ticker": "TSLA",
            "company": "Tesla",
            "action": "AVOID",
            "score": 34.9,
            "strategy": "Breakout Volume V1",
            "reason": "Price sits below its trend reference and recent momentum is negative despite elevated volume.",
            "price": 438.26,
            "move": -3.2,
        },
    ],
    "positions": [
        {"ticker": "AAPL", "side": "LONG", "quantity": 42, "price": 229.41, "value": 9635.22, "pnl": 684.18, "weight": 9.1},
        {"ticker": "GOOGL", "side": "LONG", "quantity": 38, "price": 196.32, "value": 7460.16, "pnl": 412.68, "weight": 7.0},
        {"ticker": "AMD", "side": "LONG", "quantity": 55, "price": 168.07, "value": 9243.85, "pnl": -226.6, "weight": 8.7},
    ],
}


def demo_snapshot() -> dict:
    return deepcopy(_DEMO_SNAPSHOT)
