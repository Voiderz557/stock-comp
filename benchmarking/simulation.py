"""Portfolio simulation for an arbitrary strategy-like object.

`run_portfolio_simulation` intentionally does NOT reimplement portfolio
accounting: every dollar movement (position sizing, the $20,000-per-stock
cap, the $5 minimum price, leaving unused cash in cash, fees) is delegated
to the exact same, unmodified `backtesting.engine.rebalance_portfolio` /
`backtesting.engine.calculate_portfolio_value` functions the main
backtester uses. The only new code in this module is the weekly control
loop itself - `backtesting.engine.run_backtest` is hardwired to look
strategies up by name in the global registry (`strategies.registry`), so it
cannot accept an ad-hoc strategy object (needed for ML rankings and the
Regime Switching meta-strategy below) or a fixed Top-N cutoff. This loop is
a faithful, side-by-side copy of `run_backtest`'s own loop, parameterized
instead of hardwired.

`MLRankingStrategy`, `RegimeSwitchingStrategy`, and the research-only
`AdaptiveParticipationStrategy` accepted by this loop are non-registry
"strategy-shaped" objects:

- `MLRankingStrategy` wraps an already-fitted `ml.models` pipeline so it can
  be ranked by `backtesting.engine.rank_buy_candidates` (also unmodified)
  exactly like any registered strategy; every ticker with computable
  features is returned as a `Signal="BUY"` candidate, and the caller
  (`run_portfolio_simulation`'s `top_n` parameter) decides how many of the
  top-ranked candidates to actually hold. This is NOT new portfolio
  accounting - it is the same "rank everything, then buy down the ranked
  list" pattern every existing strategy already uses, just with a `top_n`
  cutoff applied before the money-moving step.
- `RegimeSwitchingStrategy` delegates every decision to whichever
  *currently registered* strategy `market.strategy_selector.recommend_strategy`
  prefers for the regime classified (via the existing, unmodified
  `market.regime` module) as of each rebalance date. It duplicates no
  strategy logic at all - it only chooses which existing strategy's
  `analyze()`/`rank_key()` to call that week.
- `AdaptiveParticipationStrategy` lives in `benchmarking.participation` and
  delegates stock selection to Controlled Breakout V1.  Its optional
  `select_candidates()` hook limits deployment after point-in-time regime and
  breadth have been calculated; registered strategies never use this hook.
"""

from __future__ import annotations

import json
import numpy as np
import pandas as pd

from backtesting.engine import (
    benchmark_coverage_error,
    calculate_portfolio_value,
    get_ticker_data,
    get_trade_price,
    get_valuation_price,
    rank_buy_candidates,
    rebalance_portfolio,
)
from data.ticker_history import get_ticker_identity
from config import (
    BACKTEST_BENCHMARK,
    BACKTEST_FEE_RATE,
    BACKTEST_STARTING_CASH,
    MAX_POSITION_VALUE,
    MIN_STOCK_PRICE,
    POSITION_LIMIT_MODE,
)
from data.historical_universe import get_historical_universe
from market.regime import REQUIRED_HISTORY_DAYS as REGIME_REQUIRED_HISTORY_DAYS
from market.regime import classify_market_regime, compute_market_features
from market.breadth import compute_market_breadth
from market.strategy_selector import describe_availability, recommend_strategy
from ml.feature_cache import get_feature_cache
from ml.features import REQUIRED_HISTORY_DAYS as ML_REQUIRED_HISTORY_DAYS
from ml.features import (
    InsufficientHistoryError,
    compute_feature_row,
    register_price_universe,
    truncate_to_as_of,
)
from ml.models import predicted_positive_probability
from strategies.registry import available_strategy_names, get_strategy, invoke_analyze


def run_portfolio_simulation(
    strategy,
    start_date,
    end_date,
    downloaded_data,
    benchmark=BACKTEST_BENCHMARK,
    starting_cash=BACKTEST_STARTING_CASH,
    fee_rate=BACKTEST_FEE_RATE,
    min_stock_price=MIN_STOCK_PRICE,
    max_position_value=MAX_POSITION_VALUE,
    position_limit_mode=POSITION_LIMIT_MODE,
    top_n=None,
    method_name=None,
    universe=None,
    excluded_tickers=(),
    progress_callback=None,
):
    """Simulate one method's weekly-rebalanced long-only portfolio.

    `strategy` must expose `.analyze(ticker, historical_data)` and
    `.rank_key(result)`, exactly like a `strategies.registry.StrategyDefinition`
    - a registered strategy object works here unmodified. `top_n`, if given,
    keeps only the top `top_n` ranked BUY candidates each rebalance (used
    for the ML Top-5/10/20 simulations); `None` buys every ranked BUY
    candidate, matching `backtesting.engine.run_backtest`'s own behavior for
    rule-based strategies. Unused cash is always left as cash - there is no
    forced minimum number of holdings. `universe`, if given, replaces the
    default point-in-time `data.historical_universe.get_historical_universe`
    lookup with a fixed ticker list every rebalance (handy for fast,
    deterministic tests); `None` matches `run_backtest`'s normal behavior.
    `excluded_tickers` is an explicit research-only filter applied *after*
    resolving that date's universe. It never changes point-in-time membership
    history and defaults to no filtering.
    """
    start_date = pd.Timestamp(start_date)
    end_date = pd.Timestamp(end_date)
    benchmark = benchmark.strip().upper()
    excluded = {str(ticker).strip().upper() for ticker in excluded_tickers if str(ticker).strip()}

    benchmark_data = get_ticker_data(downloaded_data, benchmark)
    if benchmark_data is None:
        raise RuntimeError(f"{benchmark} benchmark data is unavailable for this period.")

    coverage_problem = benchmark_coverage_error(benchmark_data, start_date, end_date)
    if coverage_problem:
        raise RuntimeError(f"{benchmark} benchmark coverage is incomplete: {coverage_problem}")

    simulation_dates = benchmark_data.loc[
        (benchmark_data.index >= start_date) & (benchmark_data.index <= end_date)
    ].index
    if simulation_dates.empty:
        raise ValueError("No trading dates exist in the configured range.")

    first_date = simulation_dates[0]
    last_date = simulation_dates[-1]
    benchmark_start = get_trade_price(benchmark_data, first_date)
    if benchmark_start is None:
        raise ValueError("Benchmark has no valid opening price on the start date.")

    cash = float(starting_cash)
    holdings = {}
    trades = []
    portfolio_history = []
    regime_path = []
    candidate_attribution = []
    exposure_history = []
    previous_week = None
    rebalance_dates = []
    seen_week = None
    for date in simulation_dates:
        week = (date.isocalendar().year, date.isocalendar().week)
        if week != seen_week:
            rebalance_dates.append(date)
            seen_week = week
    total_rebalances = len(rebalance_dates)
    rebalance_number = 0

    for date in simulation_dates:
        week = (date.isocalendar().year, date.isocalendar().week)

        if week != previous_week:
            rebalance_number += 1
            if progress_callback:
                progress_callback(rebalance_number, total_rebalances, date)
            universe_snapshot = (
                list(universe) if universe is not None else get_historical_universe(date).tickers
            )
            universe_snapshot = [
                ticker
                for ticker in universe_snapshot
                if str(ticker).strip().upper() not in excluded
            ]
            spy_history = get_ticker_data(downloaded_data, "SPY", copy=False)
            qqq_history = get_ticker_data(downloaded_data, "QQQ", copy=False)
            regime_result = None
            regime_row = {"Date": date, "Regime": None, "Confidence": None, "Reason": None}
            if spy_history is not None and qqq_history is not None:
                try:
                    market_features = compute_market_features(
                        spy_history.loc[spy_history.index < date],
                        qqq_history.loc[qqq_history.index < date],
                    )
                    regime_result = classify_market_regime(market_features)
                    regime_row.update(
                        {
                            "Regime": regime_result.regime,
                            "Confidence": regime_result.confidence,
                            "Reason": regime_result.reason,
                            **{f"Market {key}": value for key, value in vars(market_features).items()},
                        }
                    )
                except ValueError as error:
                    regime_row["Reason"] = str(error)
            breadth = compute_market_breadth(downloaded_data, universe_snapshot, date)
            regime_row.update({f"Breadth {key}": value for key, value in breadth.to_dict().items()})
            regime_path.append(regime_row)

            observed_results = []

            def _record_result(result):
                observed_results.append(result)

            candidates = rank_buy_candidates(
                downloaded_data,
                date,
                universe=universe_snapshot,
                min_stock_price=min_stock_price,
                strategy=strategy,
                benchmark_data=spy_history,
                result_callback=_record_result,
            )
            participation_decision = None
            if hasattr(strategy, "select_candidates"):
                selected_stocks, participation_decision = strategy.select_candidates(
                    candidates,
                    regime_result=regime_result,
                    breadth=breadth,
                    rebalance_date=date,
                )
                regime_row.update(
                    {
                        "Participation State": participation_decision.state,
                        "Participation Top N": participation_decision.top_n,
                        "Participation Valid": participation_decision.valid,
                        "Participation Reason": participation_decision.reason,
                    }
                )
            else:
                selected_stocks = candidates[:top_n] if top_n else candidates
            selected_tickers = {row["Ticker"] for row in selected_stocks}
            candidate_ranks = {row["Ticker"]: rank for rank, row in enumerate(candidates, 1)}
            for result in observed_results:
                candidate_attribution.append(
                    {
                        "Date": date,
                        "Ticker": result.get("Ticker"),
                        "Signal": result.get("Signal"),
                        "Score": result.get("Score"),
                        "Rank Among BUY": candidate_ranks.get(result.get("Ticker")),
                        "Selected": result.get("Ticker") in selected_tickers,
                        "Reason": result.get("Reason"),
                        "Factor Details": json.dumps(
                            result.get("Factor Details", {}), default=str, sort_keys=True
                        ),
                    }
                )
            cash = rebalance_portfolio(
                downloaded_data,
                date,
                selected_stocks,
                cash,
                holdings,
                trades,
                fee_rate,
                max_position_value=max_position_value,
                position_limit_mode=position_limit_mode,
            )
            portfolio_value_after_rebalance = calculate_portfolio_value(
                downloaded_data, date, cash, holdings
            )
            exposure_history.append(
                {
                    "Date": date,
                    "Cash": cash,
                    "Invested Value": portfolio_value_after_rebalance - cash,
                    "Portfolio Value": portfolio_value_after_rebalance,
                    "Gross Exposure": (
                        (portfolio_value_after_rebalance - cash) / portfolio_value_after_rebalance
                        if portfolio_value_after_rebalance
                        else None
                    ),
                    "Holdings Count": len(holdings),
                    "Participation State": (
                        participation_decision.state if participation_decision else None
                    ),
                    "Participation Top N": (
                        participation_decision.top_n if participation_decision else top_n
                    ),
                }
            )
            previous_week = week

        portfolio_value = calculate_portfolio_value(downloaded_data, date, cash, holdings)
        benchmark_price = get_valuation_price(benchmark_data, date)
        benchmark_value = starting_cash * benchmark_price / benchmark_start
        portfolio_history.append(
            {
                "Date": date,
                "Portfolio Value": portfolio_value,
                "Benchmark Value": benchmark_value,
                "Cash": cash,
                "Holdings": holdings.copy(),
            }
        )

    benchmark_end = get_valuation_price(benchmark_data, last_date)
    benchmark_return = benchmark_end / benchmark_start - 1
    ending_value = portfolio_history[-1]["Portfolio Value"]
    total_return = ending_value / starting_cash - 1

    final_holdings = []
    for ticker, shares in holdings.items():
        ticker_data = get_ticker_data(downloaded_data, ticker)
        final_price = get_valuation_price(ticker_data, last_date) if ticker_data is not None else None
        final_holdings.append(
            {
                "Ticker": ticker,
                "Shares": shares,
                "Final Price": final_price,
                "Market Value": shares * final_price if final_price is not None else None,
            }
        )

    position_attribution = []
    attribution_tickers = sorted({trade["Ticker"] for trade in trades} | set(holdings))
    for ticker in attribution_tickers:
        ticker_trades = [trade for trade in trades if trade["Ticker"] == ticker]
        gross_buys = sum(
            trade["Shares"] * trade["Price"] + trade["Fee"]
            for trade in ticker_trades if trade["Action"] == "BUY"
        )
        net_sells = sum(
            trade["Shares"] * trade["Price"] - trade["Fee"]
            for trade in ticker_trades if trade["Action"] == "SELL"
        )
        final_row = next((row for row in final_holdings if row["Ticker"] == ticker), None)
        final_market_value = (final_row or {}).get("Market Value") or 0.0
        pnl = net_sells + final_market_value - gross_buys
        position_attribution.append(
            {
                "Ticker": ticker,
                "Gross Buy Cost": gross_buys,
                "Net Sell Proceeds": net_sells,
                "Final Market Value": final_market_value,
                "Attributed P&L": pnl,
                "Return on Gross Buys": pnl / gross_buys if gross_buys else None,
                "Trade Count": len(ticker_trades),
            }
        )

    return {
        "Method": method_name or getattr(strategy, "name", "Unknown"),
        "Top N": top_n,
        "Requested Start": start_date,
        "Requested End": end_date,
        "Actual Start": first_date,
        "Actual End": last_date,
        "Benchmark": benchmark,
        "Starting Value": starting_cash,
        "Ending Value": ending_value,
        "Total Return": total_return,
        "Benchmark Return": benchmark_return,
        "Portfolio History": portfolio_history,
        "Holdings": holdings,
        "Final Holdings": final_holdings,
        "Trades": trades,
        "Regime Path": regime_path,
        "Candidate Attribution": candidate_attribution,
        "Position Attribution": position_attribution,
        "Exposure History": exposure_history,
    }


class MLRankingStrategy:
    """A fitted `ml.models` pipeline, shaped to look like a registered strategy.

    Every ticker for which point-in-time features can be computed is
    returned as a `Signal="BUY"` candidate scored by predicted probability;
    `run_portfolio_simulation`'s `top_n` argument slices the ranked list
    down to a fixed count. `probability_log` records each distinct
    (as-of date, ticker, probability) prediction once per fitted fold, for
    later use by `benchmarking.stability` and `benchmarking.leakage_audit`.
    """

    def __init__(
        self,
        name,
        pipeline,
        feature_columns,
        categorical_columns,
        benchmark_data,
        include_strategy_features=True,
    ):
        self.name = name
        self.pipeline = pipeline
        self.feature_columns = list(feature_columns)
        self.categorical_columns = list(categorical_columns)
        self.benchmark_data = benchmark_data
        self.include_strategy_features = include_strategy_features
        self.required_history_days = ML_REQUIRED_HISTORY_DAYS
        self.probability_log = []
        # Point-in-time (ticker, as-of) scores are identical across Top-N
        # simulations of the same fitted pipeline. Cache them so Top 5/10/20
        # do not rebuild features.
        self._analyze_cache = {}

    def analyze(self, ticker, historical_data):
        historical_data = historical_data.dropna(subset=["Close"])
        if historical_data.empty:
            return None

        as_of = historical_data.index[-1]
        cache_key = (ticker, pd.Timestamp(as_of))
        cached = self._analyze_cache.get(cache_key)
        if cached is not None:
            return cached

        spy_full = self.benchmark_data.get("SPY")
        qqq_full = self.benchmark_data.get("QQQ")
        if spy_full is None or qqq_full is None:
            return None

        spy_hist = truncate_to_as_of(spy_full, as_of)
        qqq_hist = truncate_to_as_of(qqq_full, as_of)
        try:
            regime_result = classify_market_regime(compute_market_features(spy_hist, qqq_hist))
            feature_row = compute_feature_row(
                ticker,
                historical_data,
                spy_hist,
                qqq_hist,
                regime_result=regime_result,
                include_strategy_features=self.include_strategy_features,
            )
        except (InsufficientHistoryError, ValueError):
            return None

        all_columns = self.feature_columns + self.categorical_columns
        row_frame = pd.DataFrame([{column: feature_row.get(column, np.nan) for column in all_columns}])
        probability = float(predicted_positive_probability(self.pipeline, row_frame)[0])
        if not np.isfinite(probability):
            return None
        result = self._result_from_probability(ticker, as_of, feature_row, probability)
        self._analyze_cache[cache_key] = result
        return result

    def rank_key(self, result):
        return result["Score"]

    def rank_universe(
        self,
        downloaded_data,
        rebalance_date,
        universe,
        min_stock_price,
        historical_benchmark=None,
    ):
        """Score every eligible ticker with one batched `predict_proba`.

        Membership, trade-price eligibility, and the strictly-before-rebalance
        history cut are identical to `analyze()` + `rank_buy_candidates`.
        """
        del historical_benchmark
        feature_rows = []
        pending = []
        ready = []
        spy_full = self.benchmark_data.get("SPY")
        qqq_full = self.benchmark_data.get("QQQ")
        if spy_full is None or qqq_full is None:
            return []
        if not get_feature_cache()["tables_by_ticker"]:
            register_price_universe(downloaded_data)
            register_price_universe(self.benchmark_data)

        for ticker in universe:
            ticker_data = get_ticker_data(downloaded_data, ticker, copy=False)
            if ticker_data is None:
                continue
            trade_price = get_trade_price(ticker_data, rebalance_date)
            if trade_price is None or trade_price < min_stock_price:
                continue
            historical_data = ticker_data.loc[ticker_data.index < rebalance_date]
            historical_data = historical_data.dropna(subset=["Close"])
            if historical_data.empty:
                continue
            as_of = historical_data.index[-1]
            cache_key = (ticker, pd.Timestamp(as_of))
            cached = self._analyze_cache.get(cache_key)
            if cached is not None:
                ready.append(cached)
                continue
            try:
                spy_hist = truncate_to_as_of(spy_full, as_of)
                qqq_hist = truncate_to_as_of(qqq_full, as_of)
                regime_result = classify_market_regime(
                    compute_market_features(spy_hist, qqq_hist)
                )
                feature_row = compute_feature_row(
                    ticker,
                    ticker_data,
                    spy_full,
                    qqq_full,
                    as_of_date=as_of,
                    regime_result=regime_result,
                    include_strategy_features=self.include_strategy_features,
                )
            except (InsufficientHistoryError, ValueError):
                continue
            feature_rows.append(feature_row)
            pending.append((ticker, as_of, feature_row))

        if feature_rows:
            all_columns = self.feature_columns + self.categorical_columns
            row_frame = pd.DataFrame(
                [
                    {column: feature_row.get(column, np.nan) for column in all_columns}
                    for feature_row in feature_rows
                ]
            )
            probabilities = predicted_positive_probability(self.pipeline, row_frame)
            for (ticker, as_of, feature_row), probability in zip(pending, probabilities):
                probability = float(probability)
                if not np.isfinite(probability):
                    continue
                result = self._result_from_probability(
                    ticker, as_of, feature_row, probability
                )
                self._analyze_cache[(ticker, pd.Timestamp(as_of))] = result
                ready.append(result)
        return ready

    def _result_from_probability(self, ticker, as_of, feature_row, probability):
        self.probability_log.append(
            {"Date": as_of, "Ticker": ticker, "Probability": probability}
        )
        return {
            "Ticker": ticker,
            "Score": probability,
            "Signal": "BUY",
            "Reason": (
                f"{self.name} predicts a {probability:.1%} probability of "
                "beating SPY over the next 20 trading days."
            ),
            "Factor Details": {**feature_row, "Predicted Probability": probability},
        }


class RegimeSwitchingStrategy:
    """Delegates BUY/WAIT/AVOID decisions to whichever existing strategy is
    currently preferred for the regime classified as of each rebalance date.

    Every candidate produced for one rebalance date comes from the SAME
    underlying delegate (chosen once per date and cached), and
    `backtesting.engine.rank_buy_candidates` always finishes scoring every
    ticker for one date before calling `rank_key` to sort them - so it is
    safe for `rank_key` to look up "whichever delegate was last chosen" here.
    """

    name = "Regime Switching"
    CASH_DELEGATE_NAME = "CASH"

    def __init__(self, benchmark_data, fallback_strategy_name="Baseline"):
        self.benchmark_data = benchmark_data
        self.fallback_strategy_name = fallback_strategy_name
        self.required_history_days = max(
            REGIME_REQUIRED_HISTORY_DAYS,
            max(
                (get_strategy(name).required_history_days for name in available_strategy_names()),
                default=0,
            ),
        )
        self.regime_log = []
        self._chosen_by_date = {}
        self._last_chosen_name = fallback_strategy_name

    def _choose_delegate(self, as_of):
        if as_of in self._chosen_by_date:
            self._last_chosen_name = self._chosen_by_date[as_of]
            return self._last_chosen_name

        chosen_name = None
        regime_name = None
        spy_full = self.benchmark_data.get("SPY")
        qqq_full = self.benchmark_data.get("QQQ")
        if spy_full is not None and qqq_full is not None:
            spy_hist = truncate_to_as_of(spy_full, as_of)
            qqq_hist = truncate_to_as_of(qqq_full, as_of)
            try:
                regime_result = classify_market_regime(compute_market_features(spy_hist, qqq_hist))
                regime_name = regime_result.regime
                recommendation = recommend_strategy(regime_result)
                if recommendation.preferred_strategies:
                    availability = describe_availability(recommendation.preferred_strategies)
                    chosen_name = next((name for name, available in availability if available), None)
                else:
                    # A valid regime recommendation with no preferred long
                    # strategy means hold cash. It is not a classification
                    # failure and must not silently fall back to Baseline.
                    chosen_name = self.CASH_DELEGATE_NAME
            except ValueError:
                chosen_name = None

        if chosen_name is None:
            available_names = available_strategy_names()
            chosen_name = (
                self.fallback_strategy_name
                if self.fallback_strategy_name in available_names
                else available_names[0]
            )

        self._chosen_by_date[as_of] = chosen_name
        self._last_chosen_name = chosen_name
        self.regime_log.append({"Date": as_of, "Regime": regime_name, "Chosen Strategy": chosen_name})
        return chosen_name

    def analyze(self, ticker, historical_data):
        historical_data = historical_data.dropna(subset=["Close"])
        if historical_data.empty:
            return None
        as_of = historical_data.index[-1]
        chosen_name = self._choose_delegate(as_of)
        if chosen_name == self.CASH_DELEGATE_NAME:
            return {
                "Ticker": ticker,
                "Score": 0.0,
                "Signal": "WAIT",
                "Reason": "The current market regime has no preferred long strategy; hold cash.",
                "Factor Details": {
                    "As Of": as_of,
                    "Selected Strategy": self.CASH_DELEGATE_NAME,
                },
            }
        delegate = get_strategy(chosen_name)
        preferred = getattr(delegate, "benchmark_ticker", None)
        relative_benchmark = self.benchmark_data.get(preferred) if preferred else None
        if relative_benchmark is not None:
            relative_benchmark = truncate_to_as_of(relative_benchmark, as_of)
        return invoke_analyze(
            delegate.analyze,
            ticker,
            historical_data,
            benchmark_data=relative_benchmark,
        )

    def rank_key(self, result):
        if self._last_chosen_name == self.CASH_DELEGATE_NAME:
            return float(result.get("Score", 0.0))
        return get_strategy(self._last_chosen_name).rank_key(result)


# A last-known close is not automatically equivalent to cash/stock merger
# consideration.  Detect this after simulation so research fails closed without
# changing the shared portfolio accounting engine.
def unsupported_terminal_holdings(result):
    failures = []
    seen = set()
    for snapshot in result.get("Portfolio History", []):
        date = pd.Timestamp(snapshot.get("Date")).normalize()
        for ticker, shares in (snapshot.get("Holdings") or {}).items():
            identity = get_ticker_identity(ticker)
            if not shares or identity.public_end is None:
                continue
            public_end = pd.Timestamp(identity.public_end).normalize()
            key = (ticker, public_end)
            if date > public_end and key not in seen:
                seen.add(key)
                failures.append({
                    "Ticker": ticker,
                    "Last Public Date": str(public_end.date()),
                    "First Unsupported Valuation Date": str(date.date()),
                    "Reason": "Position valued after listing ended without verified terminal proceeds",
                })
    return failures
