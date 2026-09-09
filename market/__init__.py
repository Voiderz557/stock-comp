"""Market-wide regime detection and strategy recommendation.

This package is deliberately separate from `strategies/`. It never modifies
or depends on the internals of any individual strategy - it only reads
broad-market price data (SPY / QQQ) and recommends which *already existing*
strategy is best suited to current conditions.
"""
