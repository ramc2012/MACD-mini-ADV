# Entry Timing (Anticipatory vs Confirmed MACD Signals), Symbol/Contract Selection for Short-Horizon Option Buying, and Formulating the Learning Problem

Research date: 3 Oct 2026. User context: NSE stock options (~200 underlyings) + Nifty/BankNifty options, ATM contracts, 5-min/30-min MACD zero-cross, 40% stop / 30% trail, up to 4-session holds. User hypothesis under test: "MACD zero cross is an idea, but buying at the right time and right symbol even before the MACD cross gives more returns."

Note on method: about 18 search/fetch calls. Several primary PDFs (Zakamulin's anatomy paper, Brock et al.) could not be parsed directly, so some findings rely on abstracts and reputable secondary summaries (IDEAS/RePEc, CXO Advisory, Alpha Architect, journal abstracts). Nothing below is invented. Claims I could not source are listed under Gaps.

## Q1. Signal lag and the value of earlier (anticipatory) entry for MA/MACD-type rules

### Takeaway
The academic evidence says every MA/MACD rule is just a weighted average of past returns. Its lag and its noise (whipsaw) are two sides of the same weighting choice, so you cannot cut lag "for free." Studies that compare earlier-firing variants against slower, confirmed ones do not show that earlier wins. One 25-year Indian backtest found the earlier-firing MACD signal-line cross earned about one-third the per-trade return of the later zero-line cross. I found no peer-reviewed evidence that predicted or anticipatory crossovers beat confirmed ones after the cost of false signals. The evidence that does exist (practitioner indicators) is unvalidated.

### Cited Findings
- **Lag and noise are one design choice.** Zakamulin shows that any MA-based trading indicator (including MACD and price-minus-MA) can be rewritten as a single weighted moving average of past price changes or returns. Rules differ only in that weighting function, and its shape sets both responsiveness (lag) and smoothness (false-signal rate). — [Zakamulin, Market Timing with Moving Averages: Anatomy and Performance of Trading Rules (Springer 2017, ISBN 978-3-319-60970-6), summary via IDEAS/catalog](https://psnz.umt.edu.my/seal/Record/978-3-319-60970-6); [Alpha Architect summary](https://alphaarchitect.com/2016/04/the-moving-average-research-king-valeriy-zakamulin/)
- **Classic MA rules worked historically, mostly on the long side.** Brock, Lakonishok & LeBaron (J. Finance 47(5), 1992) tested MA and trading-range-break rules on the Dow, 1897–1986. Buy signals consistently produced higher returns than sell signals, and returns after buy signals were less volatile. The results were inconsistent with random walk, AR(1), GARCH-M and EGARCH nulls. — [IDEAS/RePEc](https://ideas.repec.org/a/bla/jfinan/v47y1992i5p1731-64.html)
- **Much of the published MA "edge" came from look-ahead bias.** Zakamulin (International Review of Finance, 2018) shows that the "too good to be true" MA results came from simulating trades with look-ahead bias. With the bias removed, the MA strategy is statistically indistinguishable from buy-and-hold. — [IDEAS/RePEc, Int. Rev. Finance 18(2):317-327](https://ideas.repec.org/a/bla/irvfin/v18y2018i2p317-327.html); [vLex abstract](https://international.vlex.com/vid/revisiting-the-profitability-of-855611393)
  - Relevance: an "anticipatory" entry backtest is especially exposed to this bias. If the bar that confirms the cross is used to decide entry at that bar's open or earlier, the backtest leaks the future.
- **India, Sensex 2000–2018.** Muruganandan (2020) split the data into bull and bear regimes. MACD sell signals beat the unconditional mean in bear markets before costs. On a Sharpe basis, though, returns did not compensate for the risk, which questions whether traders can consistently earn abnormal returns. — [Colombo Business Journal / Testing the Profitability of Technical Trading Rules across Market Cycles: Evidence from India](https://mgmt.cmb.ac.lk/cbj/index.php/testing-the-profitability-of-technical-trading-rules-across-market-cycles-evidence-from-india/)
- **India, Sensex/Nifty 1997–2010.** "Refined" MACD variants beat benchmark returns. — [IUP Journal of Applied Finance, Efficacy of Refined MACD Indicators](https://www.iupindia.in/1401/Applied%20Finance/Efficacy_of_Refined_MACD_Indicators.html)
- **The earlier MACD signal lost to the later one in a 25-year Indian backtest (practitioner study, 11 Jul 2025).** Setup: EOD data, Jan 2000–May 2025, no costs or slippage. On Nifty 50 constituents:
  - Signal-line cross (the earlier, faster trigger): 10,524 trades at 1.31% average per trade.
  - Zero-line cross (the later, confirmed trigger): 4,283 trades at 3.93% average per trade.
  - Long-only beat short-only and combined. Short-only lost across all indices.
  - Broader and smaller-cap universes did better: Next 50 6.02%, Smallcap 250 5.74%, Nifty 500 5.48% per trade.
  - — [share.market (PhonePe) backtest](https://www.share.market/buzz/insights/does-macd-actually-work-in-indian-markets-we-backtested-25-years-of-data-heres-what-we-found)
- **"Predicted crossover" tools exist but are untested.** Practitioner indicators fit a least-squares regression to recent MACD values and project it n bars ahead to "see crossovers before they happen." Histogram-slope rules ("current histogram bar higher than previous") are marketed as earlier entries. These are TradingView scripts with no published out-of-sample validation. — [TradingView MACD Forecast scripts](https://in.tradingview.com/scripts/macd); [LuxAlgo MACD histogram backtest page](https://www.luxalgo.com/library/indicator/igQvEEWQ-macd-histogram-backtest/)
- **ML can act as a filter on a crossover rule (meta-labeling).** A secondary classifier trained on triple-barrier outcomes of an SMA-crossover primary signal raised out-of-sample precision from 0.48 to 0.54 and accuracy from 37% to 56% (validation). The authors stress that meta-labeling needs a good primary model; on a poor one it "would likely only reduce the downside." — [Hudson & Thames, Singh & Joubert, "Does Meta-Labeling Add to Signal Efficacy?"](https://hudsonthames.org/does-meta-labeling-add-to-signal-efficacy/)

### Inferences
- **The hypothesis is only half-supported.** The literature does not support "enter before the cross" as a timing rule in itself. Earlier triggers mechanically raise false-positive rates, and the one direct Indian comparison (signal-line vs zero-line) favours the later, confirmed trigger per trade. What the literature does support is that the *conditional* value of a cross varies a lot (by regime, by universe, by meta-model filter).
  - The defensible version of the hypothesis: "at the right symbol, at the right time" a model can pick out the subset of early setups with high enough hit rates to beat the confirmed-cross baseline.
  - That has to be shown with the actual option exit rules (40% stop / 30% trail). Comparing signal hit rates is not enough.
- **For ATM option buyers, the false-signal cost is asymmetric.** An early entry that whipsaws pays spread plus theta. With a 40% premium stop, an ATM option can be stopped by ordinary noise before the cross ever happens. So the per-trade gain from earlier entry (capturing more of the move) must exceed (false-positive rate × average stop loss) + the extra spread/theta cost.
  - This argues for evaluating anticipatory entries on realized option P&L, not on whether the cross eventually occurred.
- **Look-ahead risk is acute for 5-min/30-min anticipatory signals.** Features must use only bars closed at decision time. A 30-min MACD "about to cross" must not use the partially formed 30-min bar unless the live system computes it the same way.
- **Practical test design:** compare three entry policies on the same universe and the same exit rules:
  - (a) confirmed zero-cross;
  - (b) model-predicted cross k bars ahead (for example, histogram rising and MACD within x·ATR of zero);
  - (c) (b) gated by a meta-label classifier.
  - Report the false-positive rate, average loss on false positives, average extra capture on true positives, and net P&L per day.

### Gaps
- I could not access Zakamulin's full text to quote his quantitative lag-vs-performance comparisons (for example, whether EMA/MACD-type rules with shorter lag beat SMA rules out of sample). The PDF would not parse.
- I found no peer-reviewed study that directly tests "anticipated/forecast MA or MACD crossover vs confirmed crossover" net of false signals. Zero-lag and forecast oscillators (Ehlers-style filters, linear-regression MACD) appear only in practitioner material without rigorous out-of-sample tests.
- I found no published study of intraday (5-min/30-min) MACD on NSE stock options with option-premium P&L.
- ML trend-start detection papers (e.g., change-point or regime models for trend onset) were not covered within the call budget.

## Q2. Cross-sectional selection for short-term directional trades

### Takeaway
The strongest intraday and short-horizon selection evidence points to: unusual relative volume ("stocks in play"); market-level intraday momentum (first half-hour return predicting the last half-hour, stronger on high-volume, volatile, news days); same-time-of-day return periodicity across days; and the split between overnight and intraday returns (momentum profits accrue overnight, while intraday tends to reverse momentum). Costs can erase gross edges.

### Cited Findings
- **Stocks in play (relative volume) plus opening-range breakout.** Zarattini, Barbon & Aziz (SSRN 4416622, Feb 2024): a 5-minute opening-range breakout applied only to the top 20 stocks by opening-range relative volume. Filters: relative volume ≥100% of the 14-day average, price >$5, 14-day average volume ≥1M shares, ATR >$0.50.
  - Reported result: 1,637% total return, 41.6% annualized, Sharpe 2.81, near-zero beta, 2016–2023.
  - — [CXO Advisory summary](https://www.cxoadvisory.com/?p=45379)
  - Contradicting replication: an independent replication on five indices reports "gross reproduced, net zero" (note: this was applied to indices, not to stocks in play). — [MQL5 blog replication](https://www.mql5.com/en/blogs/post/776235)
- **Market intraday momentum.** Gao, Han, Li & Zhou (JFE 2018), S&P 500 ETF 1993–2013: the first half-hour return (measured from the previous close) predicts the last half-hour return. The effect is stronger on volatile days, high-volume days, recession days and macro-news days. It holds for 10 other liquid ETFs. A simple long/short timing rule earned 6.67% a year. — [Alpha Architect summary](https://alphaarchitect.com/2014/08/attention-prop-traders-the-first-half-hour-of-trading-predicts-the-last-half-hour/); [WUSTL profile](https://profiles.wustl.edu/en/publications/market-intraday-momentum/)
- **Intraday periodicity.** Heston, Korajczyk & Sadka (J. Finance 2010) split the day into 13 half-hour intervals. Returns are negatively related to returns in the immediately preceding intervals (bid-ask bounce). They are positively related to returns at the same half-hour on prior days (lags 13, 26, 39 …), and this lasts at least 40 trading days. Volume, order imbalance, volatility and spreads show similar patterns but do not explain the return effect. — [Kellogg research page](https://www.kellogg.northwestern.edu/academics-research/research/detail/2010/intraday-patterns-in-the-cross-section-of-stock-returns/); [arXiv version](https://ar5iv.arxiv.org/html/1005.3535)
- **Overnight vs intraday (tug of war).** Lou, Polk & Skouras (JFE 2019): all abnormal momentum returns come overnight, while profits on most other strategies come intraday. Institutions trade intraday against momentum. When momentum activity is low, or institutions need to rebalance, momentum returns are bigger overnight and reverse more strongly during the day. The effect is stronger in large-cap, high-price stocks. — [LSE Research Online](https://researchonline.lse.ac.uk/id/eprint/87481); [Alpha Architect summary](https://alphaarchitect.com/2015/02/overnight-momentum-vs-intraday-momentum/)
- **Universe effect for MACD in India.** In the Indian EOD backtest above, MACD zero-cross long trades did better in broader and smaller-cap universes (Nifty Next 50 6.02%, Smallcap 250 5.74%, Nifty 500 5.48%) than in Nifty 50 (3.93%). — [share.market backtest, Jul 2025](https://www.share.market/buzz/insights/does-macd-actually-work-in-indian-markets-we-backtested-25-years-of-data-heres-what-we-found)
- **Learning to rank for cross-sectional selection.** Poh, Lim, Zohren & Roberts (Oxford-Man, arXiv 2012.07149, Dec 2020) found that learning-to-rank algorithms (e.g., LambdaMART, ListNet) used to rank assets before portfolio construction roughly tripled Sharpe ratios versus traditional cross-sectional momentum ranking. — [arXiv](https://arxiv.org/abs/2012.07149v1); [ar5iv full text](https://ar5iv.labs.arxiv.org/html/2012.07149)
  - The follow-up "Transfer Ranking" paper notes that rankers overfit on instruments with short histories, and proposes transfer learning to cope. — [arXiv 2208.09968](https://arxiv.org/pdf/2208.09968)

### Inferences
- **For the ~200 NSE F&O underlyings, the most evidence-backed "right symbol" features are:**
  - relative volume in the opening range or recent bars vs a 14-day same-time average;
  - gap and overnight return (Lou-Polk-Skouras suggests continuation in the overnight gap direction is weaker intraday);
  - return in the same half-hour slot on prior days (Heston et al.);
  - Nifty/BankNifty first-half-hour return as a market-direction feature for the afternoon (Gao et al.);
  - event or news days (higher predictability per Gao et al.).
- **Lou-Polk-Skouras matters for 1–4 session holds.** Holding overnight captures the momentum component, while intraday-only holds face institutional reversal pressure. A model should treat "hold through the close" as a decision, not a default.
- **Within-day ranking suits learning-to-rank.** Selecting the best k of ~200 candidates each day is a ranking problem. Ranking losses beat pointwise regression in the Oxford-Man evidence.

### Gaps
- I found no Indian (NSE) study replicating relative-volume / stocks-in-play, overnight-intraday decomposition or Heston-style periodicity on F&O stocks within the call budget.
- Earnings-day effects, sector momentum and relative strength vs index for short horizons in India were not sourced.
- The Zarattini et al. results come from a summary of an SSRN working paper (not peer-reviewed as of the summary). The replication contradicting it was on indices, not on the same stock universe.

## Q3. Which option contracts maximize return per unit of risk for directional buyers

### Takeaway
Across US equity and index options, higher embedded leverage (deeper OTM, shorter-dated, lottery-like underlyings) predicts lower average returns. OTM calls on lottery-like stocks are systematically overpriced. Indian regulator data (most recently Aug 2026) show that retail option buyers lose overwhelmingly, with transaction costs alone turning hundreds of thousands of gross winners into net losers. For a directional buyer, this argues against OTM / lottery contracts. ATM-to-slightly-ITM, liquid contracts on non-lottery underlyings keep more of the directional edge. I found no direct study ranking ATM vs ITM vs OTM for signal-driven, 1–4 day directional buying.

### Cited Findings
- **Embedded leverage lowers returns.** Frazzini & Pedersen ("Embedded Leverage", NBER w18558): instruments with embedded leverage (options, leveraged ETFs) offer low risk-adjusted returns. Across equity and index options of different moneyness and maturities, higher embedded leverage means lower returns. Long-low/short-high embedded-leverage portfolios have t-stats of 8.6 (equity options), 6.3 (index options) and 2.5 (ETFs). — [NBER working paper](https://nber.org/system/files/working_papers/w18558/w18558.pdf); [CXO summary](https://www.cxoadvisory.com/equity-premium/returns-for-leveraged-securities/)
- **Lottery-ness (skewness) predicts lower option returns.** Boyer & Vorkink (RFS 2014): ex-ante skewness has a strong negative relation with average equity-option returns. Spreads between skewness-sorted portfolios run 10–50% per week after risk controls. — [CXO Advisory summary](https://www.cxoadvisory.com/?p=13232); [BYU ScholarsArchive](https://scholarsarchive.byu.edu/facpub/8927)
- **Calls on lottery-like stocks underperform.** Byun & Kim (JFE 122(1), 2016): calls on the most lottery-like stocks underperform similar calls on the least lottery-like stocks by 10–20% per month. They deviate from put-call parity in the direction of overvalued calls, and the effect is stronger when sentiment is high. — [IDEAS/RePEc](https://ideas.repec.org/a/eee/jfinec/v122y2016i1p155-174.html)
- **OTM index calls lose more the further OTM they are.** Bakshi, Madan & Panayotov (2010, as summarized): average OTM index call returns are negative and fall as the strike rises. They start falling at about 5% OTM and turn negative at about 10% OTM. Other work attributes negative OTM returns mainly to the market volatility risk premium. — [summary in AUT/ACFR working paper](https://acfr.aut.ac.nz/__data/assets/pdf_file/0019/260920/HL_9_bc.pdf); [Structured Retail Products summary](https://www.structuredretailproducts.com/academy/selected-insight/79376/research-volatility-and-otm-call-returns)
- **0DTE buyers.** Vilkov (Nov 2023), "0DTE Trading Rules", studies unhedged 0DTE S&P 500 positions opened at fixed times and held to expiry. Summaries note magnified risk and return per unit of capital in 0DTE compared with longer-dated options. I did not obtain quantitative buyer results by moneyness. — [CXO Advisory equity options listing](https://www.cxoadvisory.com/equity-options/page/2)
- **India, SEBI FY25 study.**
  - 91% of individual F&O traders lost money in FY24-25.
  - Net losses rose 41% to ₹1,05,603 crore, from ₹74,812 crore in FY24.
  - Average loss was about ₹1.1 lakh per person, across about 96 lakh traders.
  - — [Moneylife](https://moneylife.in/article/91-percentage-of-retail-traders-lost-money-in-derivatives-losses-in-fo-surged-41-percentage-to-rs105-lakh-crore-in-fy25-sebi-study/77613.html); [Business Standard](https://www.business-standard.com/markets/news/net-losses-of-traders-in-fo-widens-in-fy25-sebi-study-125070701221_1.html)
- **India, SEBI study of Aug 2026 (covering FY2024-25 and FY2025-26).**
  - 87.7% of traders had net losses (down from 90.9%).
  - In the behavioral sample (n = 5,050, indicative only), 93% were exclusively option buyers and another 4% mostly buyers.
  - Options made up 92% of aggregate individual losses. Sellers had about a 44% loss incidence, but losing sellers lost far more on average (₹51.7 lakh).
  - 65.6% of traders active in all five years (FY22–FY26) lost money every year. Only 0.5% were profitable every year.
  - Transaction costs were about ₹24,859 crore (STT ₹6,645 crore, 27%). Costs turned about 4.4 lakh otherwise-profitable traders into net losers.
  - Traders active more than 100 days were 42% of the sample but 94% of turnover and 87% of losses.
  - — [The Policy Edge summary of SEBI studies](https://www.policyedge.in/p/nearly-9-in-10-individual-fo-traders-lost-money-as-participation-fell-18-sebi-studies)
- **Intraday cash traders (SEBI, Jul 2024).** Over 70% of individual intraday cash-equity traders lost money in FY23. — [Business Standard](https://www.business-standard.com/amp/markets/news/over-70-intra-day-traders-incur-losses-during-fy23-reveals-sebi-study-124072401110_1.html)
- **Market structure and SEBI reforms.** NSE index-option turnover rose about 13× from ₹10.8 trillion (FY20) to ₹138 trillion (FY24). SEBI reforms from Nov 2024 rationalised weekly index expiries, raised contract sizes, required upfront premium collection, and added expiry-day tail-risk margin. — [NISM](https://www.nism.ac.in/equity-linked-exchange-traded-derivative-contracts-the-retail-rush-and-regulatory-measures/); [Moneylife](https://moneylife.in/article/91-percentage-of-retail-traders-lost-money-in-derivatives-losses-in-fo-surged-41-percentage-to-rs105-lakh-crore-in-fy25-sebi-study/77613.html)

### Inferences
- **The user's ATM choice is defensible against OTM alternatives.** OTM / high-leverage / lottery contracts carry a well-documented negative expected-return premium (Frazzini-Pedersen, Boyer-Vorkink, Byun-Kim, Bakshi et al.). ATM gives the most gamma per rupee of time value without the deep-OTM overpricing.
  - Slightly ITM (delta about 0.55–0.65) cuts the extrinsic share of premium. That lowers the theta drag on a 1–4 day hold and makes a 40% premium stop correspond to a larger underlying move (fewer noise stop-outs).
  - This is a mechanical inference, not a sourced empirical result. It should be tested on the user's own data.
- **Avoid lottery underlyings as option-buy candidates even when they rank well on the signal.** Byun-Kim implies that calls on high-MAX / high-skew / high-sentiment stocks are overpriced by 10–20% a month (US evidence). Contract-level features should include IV rank, IV relative to realized vol, and skew. A cheap-vol filter is the natural counterweight.
- **Costs are first-order in India.** SEBI attributes the conversion of about 4.4 lakh profitable traders into losers to costs. Bid-ask spread as a percentage of premium, STT and brokerage per round trip must be in the label (see Q4). Illiquid stock-option strikes should be excluded or penalised.
- **The SEBI data show high activity concentrates losses** (>100 active days = 87% of losses). That supports fewer, higher-conviction trades selected by ranking, rather than trading every confirmed cross.

### Gaps
- I found no peer-reviewed study that directly compares ATM vs ITM vs OTM (or delta targets) for *signal-conditioned* directional option buying over 1–4 day holds, in the US or India.
- I found no academic study of NSE stock-option returns by moneyness or DTE, nor NSE bid-ask spread statistics by strike within budget.
- SEBI moneyness-level breakdowns (for example, loss rates for OTM vs ATM buyers) were not found in the summaries consulted.

## Q4. Formulating the learning problem: optimize realized trade returns, not signal validity

### Takeaway
Best practice (López de Prado's framework, with Oxford-Man ranking evidence) is to:
- label each candidate trade with the outcome of the *actual exit policy* (triple-barrier / path-dependent labels);
- use a primary rule plus a secondary meta-model that decides whether to trade and how large;
- rank candidates within each decision time with ranking losses;
- size positions from calibrated probabilities or expected returns.

Evaluate on daily portfolio P&L simulations rather than classification accuracy.

### Cited Findings
- **Triple-barrier labels.** Upper (profit-take), lower (stop-loss) and vertical (time) barriers, usually volatility-scaled. The label is the first barrier touched. This handles heteroskedasticity better than fixed-horizon, fixed-threshold labels. Source: López de Prado, *Advances in Financial Machine Learning* (2018). — [Hudson & Thames explainer](https://hudsonthames.org/does-meta-labeling-add-to-signal-efficacy/)
- **Meta-labeling.** The primary model sets side (e.g., a trend or MACD rule). A secondary ML model learns whether to act on each primary signal, trained on triple-barrier outcomes.
  - Trend-following example: out-of-sample precision rose from 0.48 to 0.54, with better risk-adjusted performance.
  - Mean-reversion example: out-of-sample accuracy rose from 17% to 63%.
  - Caveat: it needs a decent primary model.
  - Sizing from predicted probability was flagged as future work in that study.
  - — [Hudson & Thames, Singh & Joubert](https://hudsonthames.org/does-meta-labeling-add-to-signal-efficacy/)
- **Learning to rank beats pointwise prediction for cross-sectional selection** (about 3× Sharpe vs traditional momentum ranking). — [Poh et al., arXiv 2012.07149](https://arxiv.org/abs/2012.07149v1)
- **Rankers overfit on short-history instruments;** transfer learning across instruments helps. — [Poh et al., Transfer Ranking, arXiv 2208.09968](https://arxiv.org/pdf/2208.09968)
- **Backtests that leak future information give misleading MA performance.** A cautionary point for label construction and simulation. — [Zakamulin 2018, Int. Rev. Finance](https://ideas.repec.org/a/bla/irvfin/v18y2018i2p317-327.html)

### Inferences
- **Label the trade, not the signal.**
  - For each candidate (underlying, contract, decision timestamp), simulate the *exact* live exit policy on the option premium path: 40% stop, 30% trail, max 4 sessions, expiry handling, and spread, STT and brokerage.
  - The primary regression target is the realized net option return, clipped or winsorized (for example to [-40%, +X%]) to limit the influence of fat right tails.
  - Auxiliary targets: MFE (maximum favorable excursion, which reveals whether a better exit could have captured more), MAE (maximum adverse excursion, i.e. stop-out risk), and time-to-exit.
  - This is a direct application of the triple-barrier idea, with barriers set by the user's own rules instead of volatility multiples.
- **Recommended structure, a hybrid:**
  1. Candidate generation: anything near a MACD event (both "about to cross" and "just crossed"), so the model can learn whether anticipation pays. The entry-timing question becomes a feature/label question rather than a hand-coded rule.
  2. Meta-label classifier: P(net return > 0 under the exit rules), calibrated (Platt or isotonic).
  3. Within-timestamp ranker: LambdaMART / XGBoost-rank on realized net return (or return buckets) across the ~200 underlyings × call/put (× strike choice if strike selection is learned).
  4. Sizing: allocate capital to the top-k by expected net return × probability, capped by liquidity (spread, open interest) and a fractional-Kelly-style scale. Kelly use is an inference here, not a sourced finding.
- **Evaluate as a portfolio:** walk-forward by date with purging/embargo of overlapping 4-session labels (López de Prado's purged CV concept), a daily P&L simulation with the real capital constraint (e.g., Rs 1 L vs Rs 50 L books), turnover and cost accounting, and comparison against the confirmed-zero-cross baseline on the same days.
  - Key metrics: daily Sharpe, max drawdown, P&L per trade, P&L per day, hit rate, and average false-positive loss.
- **Contextual bandits** (exploration over contract choices such as delta bucket or DTE) fit naturally once live, but need many decisions per arm. With about 200 underlyings and few trades a day, offline ranking on simulated labels is likely more sample-efficient at first. This is an inference only.

### Gaps
- No sourced empirical comparison of classification vs regression-on-clipped-return vs ranking specifically for option-trade selection was found.
- No published evidence was found on contextual bandits for option contract selection.
- No sourced study on sizing from meta-label probabilities (e.g., the bet-sizing chapter of López de Prado's book) was fetched within budget. It is referenced from general knowledge only and should be checked.
- Purged k-fold / embargo is cited from general knowledge of López de Prado (2018). No primary page was fetched in this session.
