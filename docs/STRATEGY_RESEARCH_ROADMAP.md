# Stock-Comp Strategy Research Roadmap

Research date: 2026-09-26

Status: research only. Nothing in this document authorizes live trading, paper-trade execution, parameter optimization, or promotion of an invalid benchmark.

## Executive conclusion

The project does not currently need more loosely differentiated momentum strategies. It needs one controlled breakout candidate, stronger regime-aware cash behavior, better cross-sectional ranking, and a stricter research/promotion process.

The strongest immediate candidate is **Controlled Breakout V0**: keep Breakout Volume V1's signal logic, reject BUY candidates with 20-day daily volatility of at least 3.5% or price at least 20% above MA50, and rank remaining BUY candidates by `base_score / max(volatility_20d, 1%)`.

This candidate improved materially in both the 2023-2025 exploratory screen and two untouched 2022 windows. It is now registered as a research-only strategy: selectable in backtests, but deliberately excluded from live recommendations and ML features pending stronger validation.

## 2026 implementation and clean validation update

Controlled Breakout V1 was implemented with the preregistered constants unchanged: 3.5% maximum 20-day daily volatility, 20% maximum MA50 extension, and `base_score / max(volatility_20d, 1%)` ranking. Regime-path, point-in-time breadth, candidate, position-P&L, and exposure attribution are now exported by the benchmark.

Missing LITE and SNDK sessions for the 2026 holdout were recovered through the existing loader after an isolated validation attempt; the prior cache files were backed up. Four chronological, non-overlapping folds from 2026-08-18 through 2026-09-18 then passed the complete-coverage gate with no blacklist:

- Controlled Breakout V1 average return: -1.27%, versus -3.75% for Breakout Volume V1.
- Controlled Breakout V1 median return: -1.23%, versus -5.60%.
- Controlled Breakout V1 beat-SPY rate: 50%, versus 25%.
- Both methods' best fold was +3.93%; Controlled Breakout's worst fold was -6.55%, versus -7.75%.

This improves the relative result but does not meet the promotion gate because absolute average/median returns remain negative, there are only four short folds, and neither method produced a 10%+ result. Controlled Breakout remains research-only. The older ANSS, ATVI, EA, SGEN, SPLK, and WBA gaps remain unresolved for periods requiring those constituents; those periods still fail closed.

## What the current evidence says

The 20 five-month 2023-2025 run used seed 43 and the point-in-time Nasdaq-100 universe, then explicitly excluded ANSS, ATVI, EA, SGEN, SPLK, and WBA because required data was unavailable. It is therefore survivorship-biased and non-promotable.

- SPY's average five-month return was 8.10%.
- The corrected Regime Switching method averaged 1.83%; Breakout Volume V1 averaged 1.30%; Mean Reversion V1 averaged 0.98%.
- Every existing strategy had negative average excess return versus SPY.
- Existing strategy returns were highly correlated. Aggressive Momentum and Relative Strength Momentum correlated at 0.92; Baseline and corrected Regime Switching correlated at 0.94. The strategy shelf contains more naming diversity than return diversity.
- Turnover was very high: approximately 5.9x to 28.6x initial capital over a five-month test, before transaction costs.
- The worst 2025 momentum loss was not caused by an obvious cached-price discontinuity. The portfolio was almost fully invested in five high-beta recent winners immediately before multiple large daily declines.
- A real regime-switching defect was fixed: a valid DOWNTREND recommendation with no preferred long strategy had silently fallen back to Baseline instead of cash.

## Pre-registered challenger screen

Three candidates were defined once before testing on the same 20 periods.

### 1. Controlled Breakout V0 — advance

Definition:

- Start with Breakout Volume V1 without changing its base formula.
- Reject an otherwise valid BUY when 20-day daily volatility is at least 3.5%.
- Reject an otherwise valid BUY when price is at least 20% above MA50.
- Rank accepted BUY candidates by `base_score / max(volatility_20d, 0.01)`.

Exploratory 2023-2025 result:

- Average return: 4.77% versus 1.30% for Breakout Volume V1.
- Median return: 6.64% versus -1.27%.
- Mean max drawdown: -15.16% versus -13.55% for the original aggregate measure; daily portfolio volatility fell materially to 0.89%.
- Positive-period rate: 70%.
- Beat-SPY rate: 35%.
- It improved 11 of 20 periods, with a mean improvement of 3.47 percentage points.
- It produced no 20% five-month winner. The risk controls improved consistency but compressed the competition-oriented right tail.

Untouched 2022 check using two non-overlapping five-month windows:

- Controlled Breakout averaged -1.34% versus -8.48% for Breakout Volume V1.
- Worst period improved from -18.34% to -3.94%.
- Average excess return improved from -6.02% to +1.12%.
- This is encouraging but only two windows and still uses the incomplete exploratory universe.

Decision: advance to a clean implementation and broader walk-forward evaluation, but do not promote.

### 2. Defensive Regime Switching V0 — reject current form

Definition: hold cash in HIGH_VOLATILITY as well as DOWNTREND.

Result: average return fell by 0.93 percentage points versus the corrected selector that holds cash only in DOWNTREND. It improved zero periods and worsened five. A binary high-volatility cash rule is too blunt; high volatility can occur during both crashes and recoveries.

Decision: reject the blanket rule. Research a graduated breadth/trend confirmation instead.

### 3. Intermediate Relative Momentum V0 — reject current form

Definition: combine 120-to-20-day momentum, 60-to-5-day momentum, QQQ-relative intermediate momentum, MA200, and a volatility guard.

Result: average return was 0.70%, median was -2.96%, worst period was -30.34%, and it improved only 5 of 20 periods versus Relative Strength Momentum V1. Although 30% of periods exceeded 20%, results were dependent on extreme winners.

Decision: reject this formula. Retain “skip recent returns” as a possible feature, not a standalone strategy.

## Why these directions are evidence-aligned

Momentum has robust long-run evidence, but it also has predictable crash states and negatively skewed outcomes. Daniel and Moskowitz find crashes following market declines and high volatility, while volatility-managed momentum materially improves risk-adjusted behavior: [Momentum Crashes](https://www.nber.org/papers/w20439). Barroso and Santa-Clara similarly report that managing time-varying momentum risk largely eliminates crashes and nearly doubles Sharpe ratio: [Momentum Has Its Moments](https://www.sciencedirect.com/science/article/abs/pii/S0304405X14002566).

Intermediate rather than ultra-recent returns are central to conventional momentum definitions. Standard research commonly skips the most recent month because short-term reversal can contaminate momentum; the literature describes momentum as months 12 through 2 rather than simply chasing the latest winners: [Interacting Anomalies](https://academic.oup.com/raps/article/15/2/162/7979163), [Is Momentum Really Momentum?](https://www.sciencedirect.com/science/article/abs/pii/S0304405X11001152).

Market breadth is a plausible confirmation feature available from the existing point-in-time universe and OHLCV cache. Cross-country evidence finds breadth predictive after controlling for momentum, volatility, and trend signals, while warning that frequent rebalancing can erode gains: [Herding for Profits](https://papers.ssrn.com/sol3/Delivery.cfm/SSRN_ID3444882_code697423.pdf?abstractid=3444882).

Volume should remain a secondary confirmation feature. Out-of-sample evidence for volume-return prediction is weak and often economically small: [Dynamic Trading Volume and Stock Return Relation](https://www.sciencedirect.com/science/article/pii/S105752191730128X).

Most importantly, trying many thresholds against the same short history will manufacture winners. Backtest-overfitting research explicitly warns that selecting the best result from repeated trials produces false discoveries: [The Probability of Backtest Overfitting](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2326253), [Anomalies and False Rejections](https://academic.oup.com/rfs/article-abstract/33/5/2134/5739455).

## Recommended build sequence

### Phase 1 — make evaluation trustworthy

1. Resolve or explicitly license the six historical data gaps. Until then, all blacklist-mode findings stay non-promotable.
2. Add a permanent research-trial ledger recording every tested hypothesis, formula, date range, seed, and result. Failed variants must remain visible.
3. Add realistic transaction-cost sensitivity. Current turnover makes zero-fee results optimistic.
4. Report regime occupancy through each period, not only the regime on its first date.
5. Add candidate-level attribution: entry/exit dates, realized contribution, exposure, cash, volatility, extension, and rejection reason.

### Phase 2 — implement Controlled Breakout V1

Implement one readable strategy with the preregistered V0 constants unchanged. Test:

- registration and required history;
- no look-ahead;
- volatility guard boundary;
- MA50-extension guard boundary;
- risk-adjusted ranking;
- unchanged Breakout Volume V1 output;
- deterministic rankings;
- full suite.

Do not optimize the 3.5%, 20%, or 1% constants on the 2022-2025 results.

### Phase 3 — add breadth, not more near-duplicate momentum

Create a market-breadth feature from the historical point-in-time universe:

- percentage above MA50;
- percentage above MA200;
- percentage with positive 20-day momentum;
- percentage making/near prior 20-day highs.

Use breadth first as an explanatory dashboard feature. Only after validating its history should it become a conservative gate or score modifier.

### Phase 4 — build an ensemble selector

The target architecture should be:

- DOWNTREND: cash;
- HIGH_VOLATILITY: conditional exposure based on trend plus breadth, not unconditional cash;
- STRONG_UPTREND: Controlled Breakout candidate, with Relative Strength as confirmation;
- WEAK_UPTREND: corrected Regime Switching / Momentum V2 candidate;
- SIDEWAYS: Mean Reversion V1, pending more than one observed sideways window.

Do not average highly correlated strategy scores and call that diversification. Require incremental out-of-sample value or meaningfully different return behavior.

### Phase 5 — only then revisit ML

ML should rank candidates only after:

- the historical universe and labels are complete;
- strategy-derived features include the new risk controls and breadth;
- the walk-forward benchmark has enough valid folds;
- the model beats both SPY and Controlled Breakout out of sample;
- feature stability and leakage audits pass.

## Promotion gate for any new strategy

A candidate should not reach the daily assistant unless all are true:

- complete point-in-time coverage, with no exploratory blacklist;
- every test and trial disclosed;
- positive median excess return versus SPY;
- beat-SPY rate of at least 55%;
- acceptable worst-period loss and drawdown versus the incumbent;
- performance across multiple non-overlapping market regimes;
- no dependence on one or two extreme winners;
- survives plausible trading-cost assumptions;
- passes a genuinely untouched holdout or forward paper period;
- all leakage, deterministic-output, and regression tests pass.

## Data upgrades ranked by value

1. Complete adjusted OHLCV and corporate-action history for historical constituents.
2. Sector classifications for concentration controls and sector-relative strength.
3. Earnings-calendar dates for event-risk avoidance; no textual news analysis is required initially.
4. Fundamental quality inputs only after reliable point-in-time filing data is available.

Do not add fundamentals, earnings, or sector controls from present-day snapshots to historical rows. That would create survivorship or look-ahead bias.

## Current recommendation

Build Controlled Breakout V1 next, exactly as preregistered. In parallel, improve attribution and regime-path reporting. Do not add another general momentum formula, optimize thresholds, or activate ML/live trading yet.

## Adaptive Participation V1 development update

Adaptive Participation V1 is now available only in the ML-vs-strategy research benchmark. It is deliberately absent from the live strategy registry, competition recommendations, and ML strategy features. It delegates every candidate score and signal to unchanged Controlled Breakout V1, then selects a maximum of zero, two, or five candidates:

- INVALID when regime/breadth inputs are missing or eligible breadth coverage is below 80%; the benchmark fails instead of recording a cash return.
- OFF with zero positions in DOWNTREND, or when both percentage-above-MA50 and percentage-positive-20D-momentum are below 40%.
- HALF with two positions in HIGH_VOLATILITY or SIDEWAYS, when either SPY/QQQ is below MA200, or when either breadth measure is below 60%.
- FULL with five positions otherwise.

The constants were recorded before the first development run. On the same four previously used 2026 folds, Adaptive Participation averaged -0.57% versus -1.27% for Controlled Breakout V1, improved the worst fold from -6.55% to -3.61%, and reduced average turnover from 1.47x to 0.60x. Beat-SPY and positive-period rates remained 50%, and neither method produced a 10% return. This is not a fresh holdout and does not justify promotion. The next test must keep these constants fixed and use additional complete periods or forward shadow data.

All tested variants, including rejected ones, are recorded in `RESEARCH_TRIAL_LEDGER.csv` to make multiple-testing history visible.

### First forward-shadow observation

The frozen controller was evaluated on the first complete data after the prior development endpoint: 2026-09-21 through 2026-09-25. Point-in-time constituent and benchmark coverage passed with no exclusions. On the September 21 rebalance, the regime was WEAK_UPTREND but only 32% of eligible constituents were above MA50 and 29% had positive 20-day momentum. The preregistered rule therefore selected OFF and held cash for a 0.00% return. Controlled Breakout V1 returned +1.67%, Breakout Volume V1 returned +1.71%, and SPY returned +0.67%.

This observation is evidence against the controller for that week, not a reason to alter the 40% cutoff. Five sessions provide no reliable performance estimate. Continue accumulating forward observations with unchanged constants and report both avoided losses and missed gains.

Supporting outputs:

- `benchmark_exports/exploratory-blacklist-20-20260926T213123Z/candidate_screen_v0_definitions.json`
- `benchmark_exports/exploratory-blacklist-20-20260926T213123Z/candidate_screen_v0_summary.csv`
- `benchmark_exports/exploratory-blacklist-20-20260926T213123Z/candidate_screen_v0_periods.csv`
- `benchmark_exports/exploratory-blacklist-20-20260926T213123Z/controlled_breakout_2022_holdout_summary.csv`
- `benchmark_exports/exploratory-blacklist-20-20260926T213123Z/controlled_breakout_2022_holdout_periods.csv`
