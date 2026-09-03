# Stock-Comp Master Development Plan

## 1. Project Mission

### Immediate goal
Build a robust stock-selection and portfolio-recommendation system for the **KPMG Bermuda Senior School Investment Challenge**.

The system should help decide:
- what stocks to buy,
- how much to allocate to each,
- when to sell,
- when to hold,
- when to keep cash,
- and which strategy is most appropriate for the current market environment.

### Long-term goal
After the competition, continue developing the project into a personal quantitative-trading research platform.

The first serious version will **recommend trades only**. Automatic order execution should only be considered later, after the system has been thoroughly tested and only if the trading platform supports a safe API.

---

# 2. Core Design Principles

1. **Evidence before complexity**
   - Do not add an indicator just because it is popular.
   - Every indicator or strategy must earn its place through backtesting and out-of-sample testing.

2. **No future-data cheating**
   - Every backtest must only use information that would actually have been available at that historical moment.
   - Prevent look-ahead bias and survivorship bias wherever possible.

3. **One configurable system**
   - The bot should not be hard-coded for one investment horizon.
   - It should support multiple horizons and strategies through configuration.

4. **Simple components**
   - Data loading, indicators, strategies, backtesting, portfolio construction, and UI should be separate.
   - Changes to one part should not require rewriting everything else.

5. **Competition first**
   - Development decisions during the next two months should prioritize improving performance and reliability for the KPMG challenge.
   - Interesting features that do not help the competition should wait.

6. **Understand the important parts**
   - AI tools may help write code, but the strategy logic, backtest assumptions, scoring system, and portfolio rules must remain understandable.

7. **No guarantee mentality**
   - The system is meant to improve decision quality and consistency.
   - A strong backtest does not guarantee future returns or a competition win.

---

# 3. Investment Horizons

The system should eventually support:

- 1 month
- 2 months
- 3 months
- 6 months
- 1 year
- 2 years
- 3 years
- 5 years
- 10 years

These horizons should not simply reuse identical settings.

For example:

- Short horizons may emphasize recent momentum, breakouts, volume, and faster trend signals.
- Medium horizons may use momentum, trend, volatility, relative strength, and broader confirmation.
- Long horizons may place more weight on persistent trends, quality/fundamental information, valuation, and slower signals.

The six-month KPMG competition should have its own tuned configuration.

Example future configuration:

```python
TIMEFRAME = "6_month"

TIMEFRAME_SETTINGS = {
    "1_month": {...},
    "3_month": {...},
    "6_month": {...},
    "1_year": {...},
}
```

Do not manually duplicate entire algorithms for every horizon. Keep the strategy code reusable and change parameters through configuration.

---

# 4. Strategy Architecture

The project should eventually contain several strategy families rather than one giant score containing every indicator ever invented.

Initial strategy candidates:

### A. Momentum
Looks for stocks already outperforming over several recent periods.

Possible inputs:
- 5-day return
- 20-day return
- 60-day return
- 120-day return
- relative strength versus the market

### B. Trend following
Looks for sustained upward trends.

Possible inputs:
- price versus moving averages
- moving-average slope
- moving-average crossovers
- trend persistence

### C. Breakout
Looks for stocks moving through important recent highs.

Possible inputs:
- 20-day high
- 50-day high
- breakout distance
- breakout volume confirmation

### D. Mean reversion
Looks for temporary moves away from a normal range.

Possible inputs:
- RSI
- distance from moving average
- short-term overextension

This should initially be treated as a separate strategy, not mixed blindly into momentum.

### E. Multi-factor ranking
Combines several proven signals to rank stocks against one another.

Possible factors:
- momentum
- trend
- relative volume
- volatility
- breakout strength
- relative strength

### F. Market-regime selector
Later, create a component that decides which strategy or strategy mix is most suitable for current conditions.

Example market states:
- strong bull trend
- weak bull trend
- sideways/choppy
- high-volatility selloff
- recovery/reversal

The regime selector should be tested historically. It must not simply contain subjective rules that happen to describe the current market.

---

# 5. Indicators to Research

Candidate indicators include:

- momentum over several periods
- SMA / EMA
- relative strength
- relative volume
- volatility
- ATR
- RSI
- MACD
- recent highs / breakout distance
- drawdown
- market trend
- sector strength

Later possibilities:
- fundamentals
- earnings data
- analyst revisions
- news/sentiment

## Rule for adding indicators

Every new indicator should go through this process:

1. Define exactly what it measures.
2. Create the indicator cleanly.
3. Backtest strategy without it.
4. Add the indicator.
5. Backtest again.
6. Compare performance and robustness.
7. Keep it only if it provides useful improvement across multiple periods.

Do not judge an indicator from one successful month.

---

# 6. Stock Universe

The final system should automatically scan a large stock universe rather than requiring one ticker at a time.

Start with a manageable, liquid universe such as:
- S&P 500,
- Nasdaq-100,
- or another list allowed by the competition.

Later expand if the official competition rules permit it.

Required filters should eventually include:
- minimum price,
- minimum liquidity,
- sufficient historical data,
- valid listing,
- competition eligibility.

The exact filters must be updated after receiving the official KPMG challenge rules.

---

# 7. Stock Scoring and Ranking

The bot should ultimately compare stocks with each other instead of only producing a simple BUY/WAIT/AVOID score.

Desired output example:

```text
RANK  TICKER   SCORE   SIGNAL
1     ABC      88.4    STRONG BUY
2     XYZ      83.7    BUY
3     DEF      80.1    BUY
4     QRS      74.2    WATCH
```

A future 0-100 score might combine normalized factors.

Example only:

```python
WEIGHTS = {
    "momentum": 0.30,
    "trend": 0.20,
    "relative_strength": 0.15,
    "volume": 0.15,
    "breakout": 0.10,
    "risk": 0.10,
}
```

These weights are placeholders. They must be tested, not assumed correct.

---

# 8. Portfolio Recommendation Engine

The bot should eventually tell the user exactly what action it recommends.

Example:

```text
TODAY'S RECOMMENDATION

BUY
ABC    18% allocation
XYZ    17%
DEF    15%

HOLD
QRS    12%

SELL
LMN    entire position

CASH
38%

Reason:
- Market regime: cautious bullish
- ABC ranked #1 across momentum, trend and relative volume
- LMN fell below the portfolio exit threshold
```

The portfolio engine should decide:

- which stocks to own,
- target allocation,
- maximum position size,
- total number of positions,
- cash allocation,
- rebalance frequency,
- entry rules,
- exit rules,
- replacement rules,
- risk limits.

Short selling should not be implemented until competition rules confirm it is allowed and the long-only system is reliable.

---

# 9. Backtesting Engine

This is the most important technical component.

The backtester must simulate what would have happened historically using only information available at that time.

## Required features

- configurable start/end dates
- starting balance
- transaction fees
- realistic trade timing
- position sizing
- rebalance rules
- portfolio history
- cash tracking
- benchmark comparison
- trade log
- no look-ahead

## Required statistics

At minimum:

- total return
- annualized return where appropriate
- maximum drawdown
- volatility
- Sharpe ratio
- win/loss statistics
- number of trades
- turnover
- best and worst periods
- benchmark return
- excess return versus benchmark

For the KPMG system, also report:
- six-month return,
- rank of strategy variants,
- worst six-month period,
- median six-month result,
- percentage of historical six-month tests that beat the benchmark.

---

# 10. Testing Method

Do not optimize on one historical period.

Use three categories:

### Training periods
Used to develop and tune parameters.

### Validation periods
Used to compare strategy versions while developing.

### Final out-of-sample periods
Kept untouched until major decisions are complete.

Also use rolling / walk-forward testing.

Example:

```text
Train: 2017-2020
Validate: 2021
Test: 2022

Then move forward:

Train: 2018-2021
Validate: 2022
Test: 2023
```

The exact structure can be refined later.

---

# 11. Optimization

Once the backtester is trustworthy, build an optimizer.

It may test:
- momentum periods,
- moving-average lengths,
- factor weights,
- rebalance frequency,
- number of holdings,
- exit thresholds,
- volatility limits.

Example:

```text
momentum_lookback: 20, 40, 60, 90
holdings:          5, 8, 10, 15
rebalance:         daily, weekly, biweekly
```

## Critical rule
Do not simply choose the combination with the highest historical return.

Prefer settings that:
- perform well across many periods,
- are not extremely sensitive to tiny parameter changes,
- survive out-of-sample testing,
- produce reasonable drawdowns,
- remain effective in different market regimes.

The optimizer should help find robust settings, not manufacture a perfect-looking historical chart.

---

# 12. Market-Regime System

This is a later-stage feature.

Goal:
Determine which strategy is currently most appropriate.

Potential regime inputs:
- S&P 500 trend
- index versus moving averages
- broad-market momentum
- volatility level
- breadth / proportion of stocks above key moving averages
- sector dispersion

Possible output:

```text
MARKET REGIME: STRONG UPTREND

Preferred strategy mix:
Momentum:     50%
Trend:        30%
Breakout:     20%
Mean reversion: 0%
```

The regime system itself must be backtested.

---

# 13. User Interface

The interface should prioritize decisions over raw data.

The user should be able to understand, within a few seconds:

1. What is the market regime?
2. What does the bot recommend?
3. Which stocks rank highest?
4. Why?
5. What trades should be made?
6. How has the strategy performed historically?

## Initial dashboard

```text
┌──────────┬──────────┬──────────────┬──────────────┐
│ Signal   │ Price    │ 5-day mom.   │ 20-day mom.  │
├──────────┴──────────┴──────────────┴──────────────┤
│          Close price + 20-day average             │
├────────────────────────┬───────────────────────────┤
│      Volume chart      │ Recent five-day table     │
└────────────────────────┴───────────────────────────┘
```

## Later dashboard sections

### Market
- market regime
- benchmark performance
- volatility

### Scanner
- ranked stock list
- scores
- signal explanations
- filters

### Stock detail
- chart
- indicators
- factor contributions
- historical signal performance

### Portfolio
- current holdings
- recommended holdings
- exact trades
- allocations
- cash
- portfolio return

### Backtest
- equity curve
- benchmark
- drawdown
- statistics
- trade history
- parameter settings

### Strategy Lab
- choose strategy
- choose timeframe
- change parameters
- run comparison
- optimizer results

Do not choose the final UI technology yet.

For the first stages, Matplotlib is acceptable because the priority is strategy correctness.

Once the engine is stable, evaluate:
- Streamlit
- Dash
- a custom web app

Streamlit will likely be the easiest next step for an interactive Python dashboard.

---

# 14. Proposed Project Structure

Do not create every file immediately. Grow toward this structure as features are needed.

```text
stock-comp/
│
├── main.py
├── config.py
│
├── data/
│   ├── loader.py
│   └── universe.py
│
├── indicators/
│   ├── momentum.py
│   ├── trend.py
│   ├── volume.py
│   ├── volatility.py
│   └── technical.py
│
├── strategies/
│   ├── momentum.py
│   ├── trend.py
│   ├── breakout.py
│   ├── mean_reversion.py
│   └── multifactor.py
│
├── portfolio/
│   ├── ranking.py
│   ├── allocation.py
│   └── risk.py
│
├── backtesting/
│   ├── engine.py
│   ├── metrics.py
│   └── reports.py
│
├── optimization/
│   ├── optimizer.py
│   └── walk_forward.py
│
├── regime/
│   └── detector.py
│
├── ui/
│   └── dashboard.py
│
├── tests/
│
├── results/
│
├── logs/
│
└── DEVELOPMENT_PLAN.md
```

Do not migrate to this whole structure on Day 1. Refactor gradually as the codebase grows.

---

# 15. Eight-Week Development Roadmap

Expected work time:
- minimum: about 90 minutes per day
- normal target: about 2 hours per day

Priority is reliable progress, not maximizing lines of code.

---

## WEEK 1 — Clean Foundation

### Goals
- understand current code
- clean UI
- separate strategy logic from display logic
- establish project configuration

### Tasks
- finish current dashboard redesign
- clean `main.py`
- clean `strat.py`
- add comments/documentation where useful
- add error handling for invalid tickers / missing data
- create `config.py`
- move strategy parameters into configuration
- learn basic Git workflow
- commit stable versions

### Deliverable
A clean single-stock analyzer that can:
- load a ticker,
- calculate current indicators,
- display them clearly,
- generate a simple signal.

Do not chase advanced strategy performance yet.

---

## WEEK 2 — Multi-Stock Scanner

### Goals
Move from analyzing one stock to ranking many stocks.

### Tasks
- create stock universe
- download data efficiently
- handle missing/error data
- calculate indicators for each stock
- produce a ranking table
- add filtering for liquidity and usable history
- export rankings to CSV

### Deliverable

```text
Top Stocks
1. ABC  84
2. DEF  81
3. XYZ  78
...
```

with understandable reasons behind each score.

---

## WEEK 3 — Backtester Version 1

### Goals
Build a trustworthy historical simulator.

### Tasks
- starting cash
- historical dates
- buy/sell simulation
- position tracking
- portfolio value
- transaction costs
- daily portfolio history
- benchmark comparison
- trade log

### Deliverable
Run a historical six-month simulation and produce:
- ending balance,
- return,
- benchmark return,
- trades,
- equity graph.

This week is more important than adding RSI or MACD.

---

## WEEK 4 — Backtest Validation and Risk

### Goals
Make sure the backtest is not lying.

### Tasks
- inspect for look-ahead bias
- verify signal dates manually
- test transaction timing
- test missing data
- calculate maximum drawdown
- calculate volatility
- add portfolio position limits
- test several historical six-month periods
- build automated tests

### Deliverable
A backtester you trust.

Do not move to optimization until this is reliable.

---

## WEEK 5 — Strategy Research

### Goals
Compare real strategy families.

### Build/test
- momentum
- trend following
- breakout
- multi-factor
- optionally simple mean reversion

### Research candidate indicators
- RSI
- MACD
- volume
- ATR
- relative strength
- volatility

For each:
- baseline test
- test with indicator
- compare
- document result

### Deliverable
A strategy comparison report showing what actually works best historically.

---

## WEEK 6 — Optimization and Timeframes

### Goals
Make the bot configurable across investment horizons.

### Tasks
- implement timeframe configurations
- parameter sweeps
- compare 1m / 2m / 3m / 6m / 1y etc.
- create training/validation/test splits
- add walk-forward testing
- identify robust parameter ranges

### Deliverable
A configuration system that can run:

```python
run_strategy(timeframe="6_month")
```

without rewriting the strategy.

---

## WEEK 7 — Portfolio Intelligence + Regimes

### Goals
Turn stock scores into exact recommendations.

### Tasks
- position sizing
- entry rules
- exit rules
- cash rules
- replacement logic
- portfolio ranking
- basic market-regime detection
- test whether regime switching improves results

### Deliverable

```text
Recommended Trades

BUY  ABC  $18,000
BUY  DEF  $16,000
HOLD XYZ
SELL QRS
CASH $22,000
```

with reasons.

---

## WEEK 8 — Competition Preparation

### Goals
Stop inventing features. Make it dependable.

### Tasks
- freeze the main strategy candidate
- run final out-of-sample tests
- stress-test bad markets
- confirm all KPMG rules
- implement competition restrictions
- test daily workflow
- improve dashboard
- create automatic daily report
- create backup strategy
- document every assumption
- ensure results are reproducible

### Deliverable
Competition-ready recommendation system.

---

# 16. Daily Work Structure

For a 90-120 minute session:

### 10 minutes
Review:
- previous work
- current bug/task
- goal for this session

### 45-60 minutes
Build one focused feature.

### 20-30 minutes
Test it.

### 10-15 minutes
Understand/document:
- what changed,
- why it works,
- what remains uncertain.

### Final 5 minutes
Commit working code to Git.

Avoid spending an entire session making the UI prettier while the backtester remains unfinished.

---

# 17. AI / Codex Rules

Codex should be treated as a coding assistant, not as the owner of the strategy.

Useful prompt to keep in project instructions:

```text
This project is a quantitative stock-selection and portfolio recommendation system.

Priority:
1. correctness
2. testability
3. understandable code
4. competition performance
5. UI polish

Before making major changes:
- inspect existing code,
- explain the intended change,
- keep strategy logic separate from UI,
- do not silently change trading assumptions,
- do not add indicators unless requested,
- avoid unnecessary abstractions,
- keep functions small and testable.

For backtesting:
- actively check for look-ahead bias,
- do not use future information,
- preserve realistic trade timing,
- document assumptions.

I am learning while building this, so explain important financial and Python concepts clearly. However, the main project objective is to build the strongest reliable system possible for the KPMG competition.
```

---

# 18. Competition Rule Checklist

Before competition configuration is finalized, obtain the official current-year rules and record:

- exact start date
- exact end date
- starting capital
- securities allowed
- exchanges allowed
- ETFs allowed?
- penny-stock restrictions?
- minimum share price?
- short selling allowed?
- margin allowed?
- options allowed?
- maximum position size?
- minimum diversification?
- trade limits?
- transaction fees?
- delayed or real-time pricing?
- market/limit orders?
- trading hours?
- scoring method
- tie-breaking rules
- whether each school can claim only one prize
- any mentor/adviser restrictions
- any restrictions on algorithms or automation

Create a `competition_rules.md` file once these are confirmed.

Do not infer rules from unrelated Wall Street Survivor public competitions.

---

# 19. Success Criteria

By the competition-ready deadline, the system should be able to:

- [ ] scan the allowed stock universe automatically
- [ ] calculate tested indicators
- [ ] run multiple strategies
- [ ] rank stocks
- [ ] adapt settings to the selected investment horizon
- [ ] backtest without known look-ahead bias
- [ ] compare against a benchmark
- [ ] calculate risk statistics
- [ ] recommend exact portfolio allocations
- [ ] explain each recommendation
- [ ] keep a trade log
- [ ] compare strategy variants
- [ ] run out-of-sample tests
- [ ] use current KPMG competition restrictions
- [ ] provide a clear daily dashboard/report
- [ ] reproduce results consistently

Optional before competition:
- [ ] regime-based strategy selector
- [ ] automated parameter optimization
- [ ] Streamlit dashboard

Post-competition:
- [ ] automatic broker/API execution
- [ ] fundamentals
- [ ] news/sentiment
- [ ] more advanced statistical/ML models

---

# 20. Features Explicitly Deferred

Do not prioritize these during the early development period:

- neural networks
- LSTMs
- reinforcement learning
- automated live-money execution
- complicated news NLP
- dozens of technical indicators
- ultra-high-frequency trading
- elaborate web UI before the core engine works

They may become useful later, but they are not required to build a strong competition system.

---

# 21. Next Immediate Steps

The next tasks are:

1. Finish the dashboard refactor already underway.
2. Keep the current simple signal as a temporary baseline.
3. Create `config.py`.
4. Move configurable values such as momentum periods and moving-average length into it.
5. Create the first proper multi-stock scanner.
6. Obtain the official current-year KPMG competition rules.
7. Begin the backtesting engine before spending significant time adding RSI, MACD, or other indicators.

The current 3-point momentum strategy is a **baseline**, not the final algorithm.

Its job is to provide something simple against which every future improvement can be measured.

---

# 22. Project Decision Log

Whenever a major strategy decision is made, record:

```text
Date:
Decision:
Reason:
Evidence:
Backtest result:
What would make us reverse this decision?
```

This prevents strategy changes from becoming random reactions to whatever the market did yesterday.

---

## Final Project Philosophy

The strongest version of this project will not be the one with the most indicators.

It will be the one that:
- has clean data,
- uses signals that survive testing,
- avoids hidden backtest errors,
- adapts sensibly to the selected horizon,
- manages portfolio risk,
- and produces clear actionable recommendations.

Build the measurement system first. Then let evidence decide how sophisticated the algorithm deserves to become.
