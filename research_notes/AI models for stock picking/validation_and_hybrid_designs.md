# Validating ML/AI trading models and hybrid ML + rule-based designs (framed for a MACD system on NSE equities / Nifty options)

Context used for framing (from the user's own project notes, not web sources): the MACD system has two paper books (Rs 1 L and Rs 50 L "smart lanes"), a GBM ranker that skips the worst-scored 30% of MACD signals, dispersion-based sizing, a 72-combination exit "levers grid" chosen on one half and tested on the other, and a 14:45 CE/PE option-picking agent. The 2 Oct 2026 study covered 25 Aug to 1 Oct 2026 (3,298 signals, 19 out-of-sample days for the Rs 50 L book; GBM walk-forward AUC 0.585 vs 0.547 for the live logistic model; option-agent first walk-forward 12 test days, t < 1). Findings there: allocation > entry filter > exit tweaks. Those internal numbers are used below only to make the worked examples concrete.

## Q1. Backtest overfitting: evidence and remedies (PBO, DSR, multiple-testing thresholds, post-publication decay)

### Takeaway
The evidence is consistent: backtested Sharpe ratios barely predict live Sharpe (R² < 0.025 across 888 Quantopian algorithms), published anomalies lose about 26% out of sample and 58% after publication, and a new signal should clear t > 3.0 rather than t > 2.0. A retail quant can apply four concrete tools: (1) count every configuration tried; (2) compute the Deflated Sharpe Ratio against the expected maximum Sharpe of that many trials; (3) compute PBO with CSCV over the grid of configurations; (4) haircut the Sharpe ratio, by much more than 50% when the Sharpe ratio is low.

### Cited Findings
**Evidence that backtests overstate performance**
- Wiecki et al. (Quantopian) studied 888 algorithms. The backtest Sharpe ratio had "very weak correlations" with out-of-sample results (R² < 0.025). The more backtests a user ran, the larger the in-sample vs out-of-sample gap. Volatility and max drawdown predicted OOS performance better than Sharpe did. A non-linear classifier on many backtest features reached R² = 0.17 on held-out data. — [Quantpedia summary of Wiecki et al.](https://quantpedia.com/quantopians-academic-paper-about-in-vs-out-of-sample-performance-of-trading-alg/)
- McLean & Pontiff (Journal of Finance 2016) studied 97 published cross-sectional predictors. Returns were 26% lower out of sample and 58% lower after publication. They treat the 26% as an upper bound on data mining, and about 32% (58 − 26) as decay from publication-informed trading. Predictors with higher in-sample returns decayed more, and returns were higher in illiquid, high-idiosyncratic-risk stocks. — [search summary of McLean & Pontiff; PDF at Gwern](https://Www.Gwern.net/doc/economics/2016-mclean.pdf); [Counterpoint Funds copy](https://counterpointfunds.com/wp-content/uploads/2017/07/PredictabilityMcleanPontiff.pdf)
- Harvey, Liu & Zhu ("...and the Cross-Section of Expected Returns", RFS 2016) argue that, given extensive data mining, the usual t > 2.0 is not appropriate. A newly discovered factor should clear t > 3.0. — [NBER w20592](https://www.nber.org/papers/w20592); [SSRN 2513152](https://papers.ssrn.com/abstract=2513152)
- Counterpoint: a later paper argues that "most claimed statistical findings in cross-sectional return predictability are likely true", i.e. the false discovery rate is lower than the HLZ framing implies, even though effect sizes shrink. I saw only the title and abstract-level listing, so treat it as a contrasting view, not a refutation. — [arXiv 2206.15365](https://arxiv.org/pdf/2206.15365)
- Harvey & Liu ("Backtesting", JPM 2015): the common practice of discounting backtested Sharpe ratios by 50% is only a rule of thumb. Their multiple-testing haircut penalises marginal strategies heavily: an annualised Sharpe below 0.4 is almost always haircut by more than 50%, and one above 1.0 by at most about 25%. So the 50% rule is too lenient for low-Sharpe strategies. — [Harvey & Liu, Backtesting (Duke PDF)](https://people.duke.edu/~charvey/Research/Published_Papers/P120_Backtesting.PDF); [R `profit.hurdle` docs referencing HL](https://www.rdocumentation.org/link/profit.hurdle)

**Deflated Sharpe Ratio (DSR), Bailey & López de Prado (2014)**
- DSR corrects for selection bias (best of many trials), sample length, and non-normal returns. It is the Probabilistic Sharpe Ratio (PSR) evaluated at a hurdle equal to the expected maximum Sharpe ratio across N unskilled trials. — [Wikipedia: Deflated Sharpe ratio](https://en.wikipedia.org/wiki/Deflated_Sharpe_ratio); [Bailey & López de Prado PDF](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf)
- Expected maximum Sharpe ratio of N unskilled trials: E[max SR] ≈ μ + σ·[(1−γ)·Φ⁻¹(1−1/N) + γ·Φ⁻¹(1−1/(N·e))]. Here γ ≈ 0.5772 (Euler-Mascheroni), σ is the cross-sectional standard deviation of the trial Sharpe ratios, and N is the number of independent trials. — [Wikipedia: Deflated Sharpe ratio](https://en.wikipedia.org/wiki/Deflated_Sharpe_ratio); [LuxAlgo DSR concept page](https://www.luxalgo.com/library/concept/deflated-sharpe-ratio/)
- N should count distinct independent strategy variations, not every correlated permutation. Correlated trials count as fewer effective trials. — [Bailey & López de Prado PDF (tool summary)](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf)
- Standard error of the per-period Sharpe estimate with skew κ and (non-excess) kurtosis γ: SE(SR) = sqrt[(1 − κ·SR + (γ−1)·SR²/4) / T]. PSR(c) = Φ[(SR − c)/SE(SR)]. — [Portfolio Optimizer: PSR, bias adjustment, MinTRL](https://portfoliooptimizer.io/blog/the-probabilistic-sharpe-ratio-bias-adjustment-confidence-intervals-hypothesis-testing-and-minimum-track-record-length/)

**Probability of Backtest Overfitting (PBO / CSCV)**
- Bailey, Borwein, López de Prado & Zhu define PBO via combinatorially symmetric cross-validation (CSCV). It is implemented in the R package `pbo` and in Python libraries such as `fynance`. — [SSRN 2326253: The Probability of Backtest Overfitting](https://papers.ssrn.com/abstract=2326253); [CRAN pbo README](https://packages.oit.ncsu.edu/cran/web/packages/pbo/readme/README.html); [fynance PBO docs](https://fynance.readthedocs.io/en/latest/generated/fynance.research.pbo.html)
- CPCV turns one backtest into a distribution of OOS Sharpe ratios, which "enables estimation of the probability of backtest overfitting". — [LuxAlgo: PBO concept](https://www.luxalgo.com/library/concept/probability-of-backtest-overfitting.md)
- A related protocol paper exists: Arnott, Harvey & Markowitz, "A Backtesting Protocol in the Era of Machine Learning". I saw only the listing. — [SSRN 3275654](https://papers.ssrn.com/abstract=3275654)

### Inferences
- **The PBO/CSCV procedure, as implemented in `pbo` (from my knowledge of the paper; the README fetch failed, so verify there):** build a T×N matrix of per-period P&L, with one column per configuration (e.g. the 72 lever combos). Split the rows into S equal contiguous blocks (S even, e.g. 8 or 16). For every one of the C(S, S/2) ways to pick half the blocks as "in-sample": choose the best IS column, find its relative rank ω in the OOS half, and compute logit λ = ln(ω/(1−ω)). PBO is the share of splits with λ ≤ 0, i.e. the IS winner lands in the bottom half OOS. Practical bar: PBO < ~0.1–0.2 is comfortable; PBO ≥ 0.5 means the selection is no better than a coin toss.
- **Worked DSR example for this system (my calculation, approximate).** The levers grid had 72 configurations and the Rs 50 L OOS test was 19 days.
  - With N = 72 the bracket term is 0.4228·Φ⁻¹(0.9861) + 0.5772·Φ⁻¹(0.9949) ≈ 0.4228·2.20 + 0.5772·2.57 ≈ 2.41.
  - Under the null, the SE of a daily Sharpe estimate is ≈ 1/√19 ≈ 0.23. So the best of 72 unskilled configs is expected to show a daily Sharpe of about 0.55, roughly 8.8 annualised (×√252), purely from luck.
  - If the 72 configs are highly correlated (effective N ≈ 10), the bracket is ≈ 1.58 and the hurdle is ≈ 0.36 daily, about 5.7 annualised.
  - Conclusion: on 19 days, almost no realised result can be distinguished from selection luck. The pick-on-one-half/test-on-other split helps, but it does not remove this problem.
- **Practical rule set for this project:**
  - Log every configuration ever tried (grid size, abandoned ideas, model variants) in a "trial ledger" so N is honest.
  - Report DSR, not raw Sharpe, for any rule promoted to a smart lane.
  - Require t ≥ 3 (HLZ) on day-level P&L before calling an ML filter or sizing rule "proven".
  - Apply the Harvey-Liu haircut. With low Sharpe ratios, expect live performance to be well under half the backtest.
- McLean-Pontiff's finding that edges are larger in illiquid, high-idiosyncratic-risk names is a caution for options. The highest-ranked signals may sit in the least liquid strikes, where paper fills are least realistic (see Q5).

### Gaps
- I could not fetch the full DSR/PBO PDFs in readable form, so the published numerical examples (e.g. the paper's own DSR example) are not reproduced here. The formulas come from secondary sources that agree with each other (Wikipedia, LuxAlgo, Portfolio Optimizer).
- I did not verify the exact number of factors HLZ surveyed (commonly cited as 316); it is not stated here.

## Q2. Cross-validation for financial time series: walk-forward vs purged/embargoed K-fold vs CPCV; how much data is needed

### Takeaway
Standard K-fold leaks information in finance because labels overlap in time and are serially correlated. Purging (drop training samples whose label window overlaps the test window) plus an embargo (drop samples just after the test window) fixes this. CPCV builds many backtest paths, which gives a distribution of OOS Sharpe ratios instead of one. Walk-forward remains the realistic simulation. Data requirements follow from the Sharpe standard error. The binding constraint for an intraday options system is the number of independent days, not the number of trades.

### Cited Findings
- López de Prado introduced purged cross-validation (2017, *Advances in Financial Machine Learning*) because conventional CV and walk-forward give overly optimistic estimates through leakage. CPCV builds multiple train/test splits, purges overlapping samples and enforces an embargo, which yields a distribution of OOS performance. — [Wikipedia: Purged cross-validation](https://en.wikipedia.org/wiki/Purged_cross-validation); [LuxAlgo: Purged CV](https://www.luxalgo.com/library/concept/purged-cross-validation.md)
- How purging, embargo and CPCV work:
  - **Purge:** remove training observations whose labels overlap test-set labels in time.
  - **Embargo:** also remove observations that immediately follow the test set, sized at about 1% of the test-set bars as a rule of thumb.
  - **CPCV:** with n groups and r test groups there are C(n, r) splits. Example: n = 6, r = 2 gives 15 splits, 30 test groups and 5 complete backtest paths (30 ÷ 6).
  - — [Return on Effort: advanced cross-validation](https://returnoneffort.beehiiv.com/p/mitigating-overfitting-advanced-crossvalidation)
- A single walk-forward run gives exactly one OOS path. CPCV gives C(n_groups, n_test_groups) folds that can be reassembled into many OOS return series, i.e. a distribution of OOS Sharpe ratios. — [LuxAlgo PBO page](https://www.luxalgo.com/library/concept/probability-of-backtest-overfitting.md); [fynance combinatorial_purged_cv](https://fynance.readthedocs.io/en/latest/generated/fynance.data.combinatorial_purged_cv.html)
- Arian, Norouzi Mobarekeh & Seco (2024, *Knowledge-Based Systems* 305:112477), "Backtest overfitting in the machine learning era: a comparison of out-of-sample testing methods in a synthetic controlled environment", is cited by a 2025 arXiv paper as finding CPCV superior at mitigating overfitting, while walk-forward remains the industry standard for realistic trading simulation. — [arXiv 2512.12924 (citing Arian et al.)](https://arxiv.org/pdf/2512.12924). I could not access the original abstract or its numbers.
- **Minimum Track Record Length** (Bailey & López de Prado, "The Sharpe Ratio Efficient Frontier", *Journal of Risk* 2012): MinTRL(c) = [1 − κ·SR + (γ−1)·SR²/4]·(z₁₋α / (SR − c))², in observations. Negative skew and fat tails lengthen the required record. The worked example in the source needed about 48 more monthly observations on top of 136 to support an annualised Sharpe above 0.75 at 95% confidence. — [Portfolio Optimizer](https://portfoliooptimizer.io/blog/the-probabilistic-sharpe-ratio-bias-adjustment-confidence-intervals-hypothesis-testing-and-minimum-track-record-length/); [Risk.net / Journal of Risk listing](https://www.risk.net/journal-risk/2223785/sharpe-ratio-efficient-frontier)
- Trade-count rules of thumb from practitioner forums and blogs range from "30 trades" to "about 200 trades for reasonable confidence". These are not rigorous. Win rate alone says nothing about significance; the t-stat is mean / (SD/√N). — [LuxAlgo: significance of returns](https://www.luxalgo.com/library/concept/significance-of-returns/); [RB Trading 200-trade rule (blog, low rigour)](https://rbtrading.substack.com/p/the-200-trade-rule-why-most-traders)

### Inferences
- **Trade-count formula (standard statistics; my derivation).** If the per-trade Sharpe is s = mean/SD of per-trade return, then t ≈ s·√n, so n ≈ (t/s)². With s = 0.1 you need about 400 independent trades for t = 2 and about 900 for t = 3. With s = 0.2 you need about 100 and 225. Low-win-rate, right-skewed option strategies (34% win rate) typically have small per-trade s, so they sit at the high end.
- **The effective sample is days, not signals.** The 3,298 MACD option signals are strongly correlated within a day (same index direction, same theta/IV move). Treat each trading day's P&L as one observation for significance tests, or use a block bootstrap by day.
- **MinTRL on daily P&L (my calculation, kurtosis 3, zero skew, 95% one-sided, c = 0).** An annualised Sharpe of about 1.6 (daily 0.1) needs about 272 trading days, roughly 1.1 years. An annualised Sharpe of 3 (daily 0.19) needs about 76 days, roughly 3.5 months. Negative skew, e.g. from short-option or tight-stop behaviour, increases these numbers. The 12-day option-agent test and 19-day lane test are far below either requirement.
- **Recommended validation stack for this project:**
  1. Labels: triple-barrier (Q3), so label windows are explicit and purging is well defined.
  2. Model selection: purged K-fold or CPCV with a 1-day embargo. For intraday signals with a 4-session hold cap, embargo at least 4 sessions after each test block.
  3. Final check: an untouched walk-forward period plus live paper shadowing (Q5).
  4. Promotion gate: DSR > 0.95 using the trial-ledger N; PBO < 0.2 across the configuration grid; t ≥ 3 on day-level P&L or the MinTRL reached.
  5. Nightly retraining (planned for the ranker): treat each retrain schedule as one strategy and evaluate it walk-forward. Do not judge it by the in-sample fit of the latest model.

### Gaps
- No primary source I found gives a definitive trade-count table for options strategies. The formulas above are standard but not taken from a single cited source.
- I could not get the quantitative results (PBO/DSR by method) of Arian et al. 2024.

## Q3. Meta-labeling and triple-barrier labeling on top of a primary technical signal

### Takeaway
Meta-labeling keeps the rule-based signal (MACD cross) as the primary model that decides the side. A secondary ML classifier predicts the probability that this particular signal will hit its profit barrier before its stop or time barrier. That probability is used to filter trades and, more importantly, to size them. This is exactly the "ML augments, doesn't replace" design. Published evidence is positive but thin: the Hudson & Thames E-mini example improved Sharpe and drawdown for a mean-reversion rule and modestly improved precision for an SMA trend rule. Joubert (2022) shows calibrated probabilities matter for fixed sizing functions.

### Cited Findings
- **Definition.** Meta-labeling applies a secondary model whose output is read as the estimated probability that a trade is profitable, and that probability can be used to size positions. — [Wikipedia: Meta-Labeling](https://en.wikipedia.org/wiki/Meta-Labeling); [Joubert, "Meta-Labeling: Calibration and Position Sizing", JFDS (via search summary)](https://hudsonthames.org/tag/meta-labeling/)
- **Calibration (Joubert 2022, JFDS).** Probability calibration significantly improves fixed position-sizing methods, i.e. sizing functions that map probability to size directly. Sizing methods that estimate their mapping from the training data gain nothing significant from calibration. Joubert also published "Ensemble Meta-Labeling" in JFDS. — [Wikipedia: Meta-Labeling](https://en.wikipedia.org/wiki/Meta-Labeling); [Hudson & Thames meta-labeling tag](https://hudsonthames.org/tag/meta-labeling/)
- **Hudson & Thames example on S&P 500 E-mini.** Source: [Hudson & Thames: Does Meta Labeling Add to Signal Efficacy?](https://hudsonthames.org/does-meta-labeling-add-to-signal-efficacy-triple-barrier-method/)
  - Setup:
    - Primary rules: 20/50 SMA crossover (trend) and 1.5-SD Bollinger mean reversion.
    - Triple-barrier labels: profit and stop barriers at multiples of daily volatility, plus a 1-day vertical (time) barrier.
    - Secondary model: random forest tuned by grid search, with upsampling for class imbalance.
    - Features: rolling volatility (50/31/15 bars), 1–5-day autocorrelation and 1–5-day momentum; the mean-reversion version added RSI(14) and moving averages.
  - Mean-reversion OOS results, primary rule vs meta-model:

    | Metric | Primary rule | With meta-model |
    |---|---|---|
    | Precision | 0.17 | 0.20 |
    | Accuracy | 17% | 63% |
    | Sharpe | 0.34 | 0.79 |
    | Annual return | 10.75% | 16.86% |
    | Max drawdown | −20.47% | −14.93% |

  - Trend (SMA) OOS results: precision rose from 0.48 to 0.54 and accuracy from 48% to 55%. Risk-adjusted performance was described as "better", but not better on every metric.
  - Caveats the authors give:
    - Only two strategies were tested.
    - Weak primary models limit the gains.
    - No bet sizing was applied.
    - The validation and test periods differ.
- **Triple-barrier method.** The upper barrier is the profit target, the lower barrier is the stop and the vertical barrier is the maximum holding time. Labels are +1, −1 or 0 depending on which barrier is touched first. Barriers are scaled by recent volatility. — [Hudson & Thames article](https://hudsonthames.org/does-meta-labeling-add-to-signal-efficacy-triple-barrier-method/)

### Inferences
- **The project's GBM "rank skip 30%" ranker is already a meta-labeling model.** The MACD cross sets the side and the GBM scores it. Upgrades suggested by the literature:
  1. Label with triple barriers that match the live exit rules: +40%/+100% premium target, 40% stop, 4-session vertical barrier. The model then predicts the outcome under the rules actually traded, not raw forward return.
  2. Calibrate the GBM output (isotonic or Platt, fitted on purged OOS folds). Then map the probability to size with a fixed function, e.g. AFML-style size = 2·Φ(z) − 1 where z = (p − 0.5)/√(p(1−p)), or simply size ∝ (p − p_threshold). Per Joubert, calibration matters most for this kind of fixed mapping.
  3. Replace the fixed percentile cut, which drifted live (the 0.321 cut skipped only 12% on 1 Oct), with sizing by calibrated probability. That turns a fragile filter into the "allocation" lever the user's study found strongest.
- **Expectations.** The primary MACD signal should be tuned for recall (catch most real moves); the meta-model's job is precision. The GBM's walk-forward AUC of 0.585 is in the range where meta-labeling can help sizing materially while still misclassifying often. That is consistent with Hudson & Thames' modest precision gains on a trend rule.
- **Feature priority (from the H&T feature sets plus the user's study):** volatility state, short-horizon autocorrelation and momentum, and cross-sectional dispersion/breadth (already shown useful for sizing). In the user's tests, extra trend indicators added little (within-side |ρ| < 0.08), consistent with meta-labeling gains coming from context and volatility features more than more indicators.

### Gaps
- I did not retrieve peer-reviewed numbers from Meyer, Joubert & Alfeus (JFDS 2022, "Meta-labeling: theory and framework" / "architecture"), which reportedly use information-advantage vs modelling-advantage framing. Treat that framing as unverified here.
- No meta-labeling study specific to Indian equities or Nifty options was found.

## Q4. ML for regime detection and for bet sizing/allocation vs signal replacement

### Takeaway
The strongest and most replicated evidence is for risk/volatility-based allocation, not for ML signal replacement. Volatility-managed exposure raises Sharpe for equity-type risk assets and consistently cuts tail events and drawdowns. HMM or volatility-regime filters on top of MA-crossover trend rules are a well-trodden pattern with mostly positive but small-sample, single-study evidence. This lines up with the user's finding that allocation > entry filter > exits.

### Cited Findings
- **Moreira & Muir.** Portfolios that take less risk when volatility is high produce large alphas and higher Sharpe ratios across market, value, momentum, profitability and other factors, and the currency carry trade. — [search summary of Moreira & Muir](https://quantpedia.com/the-impact-of-volatility-targeting-on-equities-bonds-commodities-and-currencies)
- **Harvey et al., "The Impact of Volatility Targeting".**
  - Volatility targeting raises Sharpe for risk assets (equity, credit) but has negligible Sharpe effect for bonds, FX and commodities.
  - Across 60+ assets it consistently reduces the likelihood of extreme returns and reduces max drawdown, because left-tail events tend to occur when volatility is already high and exposure is therefore small.
  - — [Harvey et al. PDF (Duke)](https://people.duke.edu/~charvey/Research/Published_Papers/P135_The_impact_of.pdf); [Quantpedia summary](https://quantpedia.com/the-impact-of-volatility-targeting-on-equities-bonds-commodities-and-currencies)
- **QuantStart HMM regime filter.** QuantStart demonstrates an HMM regime filter in QSTrader. The HMM blocks new trades when a high-volatility regime is predicted, on top of an MA-crossover trend rule, with the aim of removing unprofitable trades and lowering volatility to raise Sharpe. — [QuantStart: Market regime detection using HMMs in QSTrader](https://www.quantstart.com/articles/market-regime-detection-using-hidden-markov-models-in-qstrader/) (page returned 502 on fetch; description from search snippet)
- **Three-state Gaussian HMM on Asian equity markets.** Parameters were fit with Baum-Welch and tested on a 4-year OOS window. Over 74% of forecast days had high regime confidence. Hybrid trend-following strategies beat buy-and-hold on Sharpe and limited drawdowns. An HMM inside a risk-parity framework lowered drawdowns OOS. — [PMR journal: HMM regime detection & tactical allocation in Asian equity markets](https://so09.tci-thaijo.org/index.php/PMR/article/view/10023)
- **Common practitioner pattern:** run trend logic in trending states, mean reversion in quiet states, and stand aside in high-volatility states. — [LuxAlgo: HMM / Markov-switching regimes](https://www.luxalgo.com/library/concept/hidden-markov-markov-switching-regimes/)
- **Wiecki et al.:** volatility and drawdown predicted OOS performance better than Sharpe. That supports risk-based rather than return-based selection and sizing. — [Quantpedia](https://quantpedia.com/quantopians-academic-paper-about-in-vs-out-of-sample-performance-of-trading-alg/)

### Inferences
- **For this system, use ML for sizing and gating before using it for entries.** In order:
  1. Volatility-scaled position size per lane, e.g. size ∝ target vol / realised Nifty or India VIX-proxy vol, with caps.
  2. Calibrated meta-label probability × dispersion multiplier for per-signal size.
  3. A coarse regime gate (2–3 states) that changes risk budget or exit looseness, not the entry signal. The user saw loose exits win only in a trending, put-favoured month and CE losing on 22 of 27 days; a regime gate is the principled home for that conditionality.
- **Regime models overfit easily.** Every extra state and feature adds trials (Q1). Fit HMMs on daily index data over multiple years (not the 6-week options sample), keep them to 2–3 states, and evaluate the gate walk-forward with purging. The side-tilt-by-trailing-P&L result is "one-regime evidence" and should be treated as such until it survives at least one regime change.
- Volatility targeting's Sharpe benefit is documented for equity-type risk assets. Long option premium already has convexity, so the main expected benefit there is drawdown and tail control, not Sharpe uplift. That is an inference, not a tested result for options.

### Gaps
- No rigorous head-to-head study found that compares "ML for entry" vs "ML for sizing" on the same primary signal. The "allocation > signal" claim rests on volatility-targeting literature, meta-labeling/sizing papers and the user's own replay.
- The QuantStart HMM article's quantitative before/after table could not be retrieved (HTTP 502).
- No India-specific (NSE/Nifty) regime-model study was retrieved.

## Q5. Paper-trading vs live gap; running a meaningful shadow test

### Takeaway
Paper fills are systematically optimistic, and the bias compounds trade after trade instead of averaging out. Practitioner reports (low-rigour sources) suggest live results run at roughly 50–70% of backtest. For Nifty options the dominant risks are fill price vs bid/ask, slippage in illiquid strikes, and the timing of the 5-minute re-check and 14:45 emission. A shadow test is meaningful only once it reaches the day-count implied by MinTRL (months, not weeks) with conservative fill rules.

### Cited Findings
- Paper trading assumes ideal fills. Live orders face spreads, depth and slippage. Every simulator assumption tends to favour the paper trader, and the resulting bias accumulates across trades. — [TradersPost blog](https://blog.traderspost.io/article/paper-trading-vs-live-trading-key-differences-and-what-to-expect) (vendor blog, low rigour)
- Claimed magnitude: strategies lose 30–50% of their edge live. A NexusFi community review found live performance at 55–70% of backtest for ES scalping strategies. The gap comes from five sources: fill assumptions, slippage underestimation, look-ahead bias, data resolution and latency. — [NexusFi: backtest-to-live performance gap](https://nexusfi.com/a/automation/backtest-to-live-performance-gap); [TradersPost](https://blog.traderspost.io/article/paper-trading-vs-live-results) (practitioner sources; not peer-reviewed; treat numbers as indicative only)
- Implementation shortfall includes commissions, transfer taxes and price impact (paper price vs actual transaction price). Paper trading misses market impact, partial fills and behavioural pressure. — [BS Capital Markets: implementation shortfall](https://www.bscapitalmarkets.com/the-implementation-shortfall-where-paper-trading-meets-real-trading.html)
- India context: SEBI's FY25 study found about 91% of individual equity-derivatives traders lost money. Their net losses widened 41% to Rs 1,05,603 crore (from Rs 74,812 crore in FY24). Unique individual F&O traders fell 20% year on year. — [The Week / PTI](https://www.theweek.in/wire-updates/business/2025/07/07/dcm92-biz-sebi-derivatives-study.html); [Business Standard](https://www.business-standard.com/markets/news/net-losses-of-traders-in-fo-widens-in-fy25-sebi-study-125070701221_1.html)
- Commentary on the SEBI data notes that institutional/algorithmic participants have execution-speed and slippage advantages over retail. — [Business Standard / search summary](https://www.business-standard.com/amp/markets/news/net-losses-of-traders-in-fo-widens-in-fy25-sebi-study-125070701221_1.html)

### Inferences
- **Fill rules for the paper books** (both smart lanes and the option agent's equity curves):
  - Buy at the ask and sell at the bid, or at mid ± half-spread if only LTP is available, plus at least 1 tick of extra slippage.
  - Never fill at the bar close that generated the signal. Fill at the next tradable quote after the 5-minute re-check or the 14:45 emission.
  - Reject or partial-fill orders above a share of displayed depth. The existing 2%-of-contract-volume cap is a reasonable start; also require a minimum OI and volume per strike.
  - Charge full Indian costs: brokerage per order, STT on option sell premium (and on intrinsic value if exercised), exchange transaction charges, SEBI fee, stamp duty and GST. The user's research model used Rs 20/order + 0.05%/0.15%; check that against current schedules.
  - Treat any gap between the paper fill and the live quote at decision time as a tracked metric.
- **Shadow-test design:**
  - Run the live engine and the paper book on identical timestamps.
  - Log the decision-time quote, the bid/ask and the simulated fill, so later live trading can measure slippage against paper.
  - Freeze the model and rules for the test window. Any change resets the clock and adds to the trial ledger.
- **Test length (from Q2 maths):**
  - A plausible true annualised Sharpe of 1.5–2 on daily P&L needs about 7–12 months of trading days to reach 95% confidence.
  - A large edge (annualised Sharpe of about 3) still needs about 3.5–4 months.
  - Comparing two variants (e.g. the Rs 1 L vs Rs 50 L books, or champion vs challenger) needs longer still, because you are testing a difference.
  - Practical compromise: set a pre-registered minimum of about 60 trading days with a kill-switch (stop if drawdown exceeds 2× backtest max DD, or if the PSR of the shadow P&L vs zero drops below 0.5 after 30 days). Promote only when the PSR reaches 0.95.
- **Expect live results below paper.** If the paper Sharpe is low, the Harvey-Liu haircut plus a 30–50% execution haircut can easily take the expected live edge to zero. The Rs 1 L book is especially sensitive because of whole-lot granularity and fixed per-order costs.

### Gaps
- I found no rigorous, peer-reviewed measurement of the paper-vs-live gap for NSE index options specifically. The 30–50% and 55–70% figures come from practitioner blogs and forums about US futures.
- I did not retrieve current official NSE/SEBI charge schedules (STT, exchange fees). The user should verify them from NSE/broker sources rather than rely on any figure here.
