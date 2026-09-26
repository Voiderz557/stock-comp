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

`MLRankingStrategy` and `RegimeSwitchingStrategy` are the two non-registry
"strategy-shaped" objects this module adds:

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
"""

from __future__ import annotations

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
    """
    start_date = pd.Timestamp(start_date)
    end_date = pd.Timestamp(end_date)
    benchmark = benchmark.strip().upper()

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
            candidates = rank_buy_candidates(
                downloaded_data,
                date,
                universe=universe_snapshot,
                min_stock_price=min_stock_price,
                strategy=strategy,
            )
            selected_stocks = candidates[:top_n] if top_n else candidates
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
    }


class MLRankingStrategy:
    """A fitted `ml.models` pipeline, shaped to look like a registered strategy.

    Every ticker for which point-in-time features can be computed is
    returned as a `Signal="BUY"` candidate scored by predicted probability;
    `run_portfolio_simulation`'s `top_n` argument slices the ranked list
    down to a fixed count. `probability_log` records every
    (date, ticker, probability) triple produced during a simulation, for
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
            self.probability_log.append(
                {"Date": as_of, "Ticker": ticker, "Probability": cached["Score"]}
            )
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
                self.probability_log.append(
                    {"Date": as_of, "Ticker": ticker, "Probability": cached["Score"]}
                )
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
                availability = describe_availability(recommendation.preferred_strategies)
                chosen_name = next((name for name, available in availability if available), None)
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
        return get_strategy(self._last_chosen_name).rank_key(result)
