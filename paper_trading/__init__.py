"""Local, simulation-only paper-trading ledger.

Nothing in this package executes real trades or talks to a broker. It only
persists simulated positions to a local SQLite database (see
`paper_trading.storage`) and computes their P&L (see `paper_trading.portfolio`).
"""
