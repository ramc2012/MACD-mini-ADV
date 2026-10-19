# Sentiment, Positioning and Order-Flow Indicators for Short-Horizon Direction Prediction (NSE stocks, Nifty/BankNifty options)

Research date: 3 Oct 2026. Scope: intraday to ~4-day horizons. Priority: features computable from the user's own tick data (LTP, bid/ask, total buy/sell qty, option-chain snapshots) or freely published NSE data. Evidence ratings used below: **Strong** (multiple peer-reviewed studies, replicated), **Moderate** (peer-reviewed but single-market/single-study, or Indian evidence thin), **Weak** (practitioner analysis, small samples, or mixed results).

---

## 1. FII/DII cash flows and NSE participant-wise F&O OI (FII index-futures long/short, client vs pro): do they predict next-day/next-week Nifty? Timing and look-ahead pitfalls

### Takeaway
Daily FII cash flows mostly *follow* returns (positive feedback trading); evidence that they predict next-day Nifty is weak and inconsistent. FII index-futures positioning (participant-wise OI) has a practitioner-documented, regime-level association with forward Nifty returns (net-long periods much better than net-short), but it is a slow, low-frequency regime filter, not a next-day timing signal, and since May 2025 FIIs have been net short every session, so the signal has been stuck in one state. All of this data is end-of-day only, so for a 14:45 entry the latest usable value is from the previous day (T-1).

### Cited Findings
- **FII cash flows mostly chase returns:** daily data from Apr 2014 to Nov 2019 with Granger tests at lags 1–4 show bidirectional causality between Nifty 50 returns and FII equity flows. The dominant pattern is return-chasing (positive feedback): FIIs buy after rises and sell after falls. There is some lag-1 evidence that flows predict Nifty returns, but the paper describes it as secondary. No DII analysis. — [Trading Behaviour of FIIs: Evidence from Indian Stock Markets (PMC)](https://pmc.ncbi.nlm.nih.gov/articles/PMC9145119/)
- **Older evidence agrees on feedback trading:** strong evidence that FIIs chase trends with positive feedback trading at the aggregate daily level, but not monthly. — [ICRIER WP 109, FII Trading Strategies](https://www.icrier.org/pdf/wp109.pdf) (via search summary)
- **Causality direction is mixed across studies:** some find FII flow is an effect, not a cause, of returns. Others find bidirectional Granger causality with the Sensex/Nifty, or only a concurrent relationship. — [ResearchGate: FII Flow and Indian Stock Market: A Causal Study](https://www.researchgate.net/publication/338884616_FII_Flow_and_Indian_Stock_Market_A_Causal_Study); [Emerald: Cause and effect between FII trading behaviour and stock market returns](https://emerald.com/insight/content/doi/10.1108/17554191211274794/full/html); [ScienceDirect: FII dynamic interactions with return and volatility](https://www.sciencedirect.com/science/article/pii/S0970389616300854)
- **FII index-futures positioning, 10-year practitioner study** (NSE participant-wise OI, 1 Sep 2016 to 11 Sep 2026, 2,476 sessions; long ratio = FII long / (long + short) index-futures contracts, smoothed over 5 sessions):
  - Nifty annualised about 6% on days after an FII net-short reading, versus about 17% after a net-long reading.
  - Of 15 short streaks lasting 20+ sessions, Nifty finished higher in 8.
  - After FIIs flipped from net short to net long, Nifty rose over the next 30 sessions more than 90% of the time.
  - The author calls this observational, "not a statistical test", and notes FII shorts go with sideways/choppy markets far more than with crashes.
  - Source published 14 Sep 2026. — [Marketcalls: What Do We Actually Know About FII Index Futures Shorts?](https://www.marketcalls.in/futures-and-options/what-do-we-actually-know-about-fii-index-futures-shorts.html)
- **Current regime (as of 11 Sep 2026):** FIIs net short Nifty index futures on every session since 13 May 2025 (333 sessions, more than twice the previous record), about 2.85 lakh contracts net short, long ratio about 11%. — [Marketcalls](https://www.marketcalls.in/futures-and-options/what-do-we-actually-know-about-fii-index-futures-shorts.html); see also [Business Standard, Jun 2026: FIIs build aggressive Nifty shorts](https://www.business-standard.com/markets/news/fiis-build-aggressive-nifty-shorts-sell-76k-lots-in-4-trading-days-126060200305_1.html)
- **Press usage:** financial press routinely reports the FII long-short ratio and retail (client) shorts as daily F&O sentiment (e.g. "FIIs long-short ratio at 5:2; retail traders build shorts", Aug 2024). This is commentary, not tested evidence. — [Business Standard F&O Insights](https://www.business-standard.com/markets/news/f-o-insights-fiis-long-short-ratio-at-5-2-retail-traders-build-shorts-124083000108_1.html)
- **Publication and timing of participant-wise OI:**
  - The report is published end of day, after market close, with no fixed release time ("check the report date rather than relying on a fixed evening release time").
  - File: `fao_participant_oi_DDMMYYYY.csv` on the NSE derivatives reports page.
  - Categories: Client, DII, FII, Pro.
  - No intraday participant breakdown.
  - — [AlgoTest: Participant Wise Open Interest](https://algotest.in/blog/participant-wise-open-interest/)
- **FII/FPI cash trade data is provisional:** it is subject to change (custodial confirmation, modifications). NSE publishes a provisional FII/DII cash report after the close. — [NSE FII/DII report page](https://www.nseindia.com/reports/fii-dii); [Zerodha TradingQnA: FII data from NSDL and NSE](https://tradingqna.com/t/fii-data-from-nsdl-and-nse-website/105390)

### Inferences
- **Look-ahead pitfall:** the user's option agent picks CE/PE at 14:45. Any FII/DII cash, participant OI or FII-derivatives statistic for day T is not available until after the close of T. A backtest must lag these features by one session (use T-1 values for a T entry). Joining on calendar date without a lag gives same-day look-ahead and will badly overstate the edge, because flows and returns are concurrently correlated.
- **Provisional vs final:** backtests should use data as first published (provisional), or accept a small mismatch. Final revised NSDL/SEBI FPI figures differ and arrive later.
- **Positive feedback trading** means a large FII cash buy on T mostly reflects T's up-move. Using it as a next-day long signal mostly captures (weak) momentum, not information.
- **The long-ratio signal is low-frequency:** about 11–17% annualised difference is about 4–5 bp/day. That is negligible against intraday option premium noise. Its plausible use is as a regime/position-size filter (e.g. allow more CE allocation when the FII long ratio is rising or flips to net long), not as a directional trigger for 1–4 day option trades.
- **Since May 2025 the FII index-futures book is persistently short**, probably partly as a hedge against cash holdings (inference; not verified). The level carries little information right now. Changes (e.g. 5-day change in FII net index-futures contracts, or a flip event) are more informative than the level.
- **Client vs pro positioning:** a common practitioner idea is that retail (client) index-option and futures positioning is a contrarian indicator. No peer-reviewed Indian study testing client/pro participant OI for next-day Nifty direction was found.

### Gaps
- No peer-reviewed study found that formally tests the participant-wise FII long-short ratio (or client/pro ratios) for next-day or next-week Nifty/BankNifty direction with out-of-sample statistics. The 10-year figures are a single practitioner analysis.
- Exact NSE publication time for the provisional FII/DII cash report and the participant OI CSV: no authoritative source found. It is commonly observed in the evening, but this was not verified.
- DII flows: no study found isolating DII daily flows as a next-day predictor.
- Whether FII index-option participant OI (calls/puts long/short, available in the same CSV) adds predictive power beyond futures: no evidence found.

---

## 2. India VIX changes, advance-decline breadth, sector breadth, and market-wide "dispersion" (share of stocks with positive signals)

### Takeaway
India VIX has documented long-run/monthly causality toward Nifty, but Indian studies find little short-run predictability of direction. Extreme VIX levels have practitioner-documented conditional patterns. Breadth and advance-decline evidence is mostly US/practitioner-based and described as weak/overrated as a standalone signal. No study was found on "share of stocks with positive signals" for India. These are best treated as regime/volatility features, not direction predictors.

### Cited Findings
- **India VIX predicts Nifty in the long run, not the short run** (study of VIX vs Nifty returns). — [Academia.edu: A Study on Relation between India VIX and Nifty Returns](https://www.academia.edu/21094585/A_STUDY_ON_RELATION_BETWEEN_INDIA_VIX_AND_NIFTY_RETURNS)
- **Monthly data, Oct 2015 – Sep 2025:** unidirectional causality from India VIX to Nifty returns, consistent with VIX as a forward-looking uncertainty indicator for *monthly* movements. — [ResearchGate: Informational Content of India VIX… 2015–2025](https://www.researchgate.net/publication/404036358_Informational_Content_Of_India_Vix_Evidence_From_Volatility_Envelopes_Causality_And_Conditional_Volatility_Of_Nifty_Monthly_Returns_During_2015-2025)
- **Practitioner:** after India VIX fell below 10, Nifty returns were flat to slightly negative (avg 1-week −0.5%, 1-month −0.1%). Small sample, broker research. — [Samco: India VIX Below 10](https://www.samco.in/knowledge-center/articles/vix-below-10-what-history-says-about-nifty-returns/)
- **Deep learning has been applied to forecasting the daily direction of India VIX changes** (IIMB Management Review, 2023). This is about predicting VIX itself, not Nifty. — [ScienceDirect: Forecasting the direction of daily changes in India VIX](https://www.sciencedirect.com/science/article/pii/S0970389623000514)
- **Breadth evidence is mixed:**
  - Practitioner analysis finds the advance-decline ratio "has not tested well and is probably overrated" standalone.
  - Extreme negative breadth near market highs preceded weak S&P 500 returns in 7 of 10 cases (US, tiny sample).
  - — [LuxAlgo: Advance/decline internals](https://www.luxalgo.com/library/concept/advance-decline-internals.md); [SentimenTrader: rare negative skew in NYSE A/D ratio](https://sentimentrader.com/blog/a-rare-negative-skew-in-the-nyse-advance-decline-ratio-)
- **Academic breadth literature exists** (index-specific breadth vs index-over-moving-average; Athens SE non-linear A/D–return interaction), but results are market-specific. — [Montclair: index-specific market breadth](https://researchwith.montclair.edu/en/publications/the-use-of-index-specific-market-breadth-and-index-over-moving-av/); [Univ. of Crete: A/D ratio and Athens SE](https://economics.soc.uoc.gr/en/book/684/non-linear-diachronic-interaction-between-the-advance-decline-ratio-index-and-the-returns-of-the-markets-general-index-empirical-evidence-from-the-athens-stock-exchange)

### Inferences
- **India VIX** is published intraday by NSE (computed from Nifty option order books), so it is usable at 14:45 without look-ahead. It is most useful for:
  1. Option-pricing regime (premium richness), which matters for buyers more than direction.
  2. Conditioning: high or spiking VIX goes with larger moves both ways and with mean reversion after spikes.

  Its value as a 1–4 day direction predictor for Nifty is not supported by Indian evidence.
- **Breadth from the user's own data:** the user records ticks for F&O stocks, so they can compute an intraday breadth series at any timestamp: % of F&O stocks above VWAP, % above prior close, % with a positive MACD histogram ("dispersion of signals"). This is look-ahead-free and cheap. With no Indian evidence, it should be validated in-sample/out-of-sample before use. It is most plausibly useful as a filter (e.g. avoid index CE when breadth diverges negatively from index strength), not as a standalone trigger.
- **Sector breadth / "signal dispersion"** (share of constituents agreeing with the index MACD signal) is a natural confirmation feature for index options in a MACD system. Its value is untested and should be tested.

### Gaps
- No Indian peer-reviewed study found on next-day/intraday Nifty direction predictability from: daily India VIX *changes* (as opposed to levels/monthly), the NSE advance-decline ratio, sector breadth, or cross-sectional "signal agreement" share.
- No source found quantifying the daily VIX-change → next-day Nifty relation (mean reversion after VIX spikes) specifically for India.

---

## 3. Order-flow measures (OFI, cumulative volume delta, trade-sign imbalance, top-of-book bid/ask quantity imbalance, total buy/sell pending quantity): short-horizon predictability, Indian studies, decay speed

### Takeaway
This is the strongest-evidence category, but almost entirely at very short horizons. OFI explains contemporaneous price changes almost linearly. Lagged order imbalance and queue imbalance predict next-tick to next-few-minute moves, and that predictability decays fast. The one NSE study (195 most active stocks) finds order-imbalance predictability strong for the first 5 minutes and gone within 30 minutes. For daily horizons, US evidence (Chordia & Subrahmanyam) shows next-day effects driven by autocorrelated imbalances. Order flow is useful for entry timing and confirmation at 14:45 (minutes), not for 1–4 day direction.

### Cited Findings
- **Cont, Kukanov & Stoikov (J. Financial Econometrics 2014; NYSE TAQ, 50 US stocks):**
  - Over short intervals, price changes are mainly driven by OFI (imbalance between supply and demand at the best bid/ask).
  - The relation is linear, with slope inversely proportional to market depth.
  - It is robust across time scales, stocks and seasonality, and implies the square-root volume-impact law.
  - It is primarily a *contemporaneous* impact result.
  - — [arXiv 1011.6402](https://arxiv.org/abs/1011.6402); [RePEc](https://ideas.repec.org/a/oup/jfinec/v12y2013i1p47-88.html)
- **Cont, Cucuringu & Zhang (Quantitative Finance 23(10), 2023):**
  - Integrating OFI across the top LOB levels ("integrated OFI") explains price impact better than best-level OFI.
  - Lagged cross-asset OFIs improve forecasts of future returns, but this lagged effect shows up at short horizons and decays rapidly.
  - — [arXiv 2112.13213](https://arxiv.org/abs/2112.13213v4); [RePEc](https://ideas.repec.org/a/taf/quantf/v23y2023i10p1373-1393.html)
- **NSE India evidence** (Finance Research Letters, "Information content of order imbalance in an order-driven market: Indian Evidence"; 195 most active NSE stocks):
  - Order-flow private information has a persistent price impact and makes short-term returns predictable.
  - Predictability is strong for the first five minutes and disappears within thirty minutes.
  - Order-flow information affects deeper LOB levels, showing informed traders beyond the best quotes.
  - — [ScienceDirect S1544612320316779](https://www.sciencedirect.com/science/article/abs/pii/S1544612320316779) (abstract via search; full text paywalled/403)
- **Queue (top-of-book quantity) imbalance:**
  - Bid/ask queue imbalance has strongly significant predictive power for the direction of the next mid-price move. — [Gould & Bonart, Queue Imbalance as a One-Tick-Ahead Price Predictor (arXiv 1512.03492)](https://arxiv.org/pdf/1512.03492)
  - Volume imbalance near +1 (or −1) predicts an up (or down) mid-price jump. Predictive power is higher the closer it is sampled to the price change and decays with time. — [arXiv 2307.15599, "Understanding the worst-kept secret of HFT"](https://arxiv.org/pdf/2307.15599)
- **Chordia, Roll & Subrahmanyam** (market-wide order imbalance, liquidity and returns) and **Chordia & Subrahmanyam** (order imbalance and individual stock returns, US daily data) are the standard references for daily-horizon imbalance effects. — [Chordia/Roll/Subrahmanyam PDF](https://www.cis.upenn.edu/~mkearns/finread/Chordia_buy-sell_orders.pdf); [Chordia & Subrahmanyam (ResearchGate)](https://www.researchgate.net/publication/222673393_Order_Imbalance_and_Individual_Stock_Returns). Neither was fetched in this session; the summary below is from the well-known abstracts:
  - Imbalances are positively autocorrelated (order splitting).
  - This produces a positive relation between returns and lagged imbalance.
  - The sign reverses after controlling for current imbalance.
- **Some market-level intraday imbalance predictability is linked to non-informational trading** (Hendershott et al., "Market Predictability and Non-Informational Trading"). — [Berkeley PDF](http://faculty.haas.berkeley.edu/hender/Market_Predictability_OIB.pdf)
- **Option-market order flow predicts option returns** (Muravyev, "Order Flow and Expected Option Returns"; internet appendix). — [dmurav.com Internet Appendix](https://www.dmurav.com/Internet%20Appendix%20to%20Order%20Flow%20and%20Option%20Returns.pdf)
- **An NSE high-frequency LOB study exists on order execution probability**, not mid-price prediction. — [American Business Review via RePEc](https://ideas.repec.org/a/ris/ambsrv/0051.html)

### Inferences
**How to compute from the user's ticks (bid, ask, bid qty, ask qty, LTP, volume, total buy/sell qty):**
- **Best-level OFI** (Cont et al.): for consecutive quote updates n−1 → n:
  - e_n = 1{Pb_n ≥ Pb_{n−1}}·qb_n − 1{Pb_n ≤ Pb_{n−1}}·qb_{n−1} − 1{Pa_n ≤ Pa_{n−1}}·qa_n + 1{Pa_n ≥ Pa_{n−1}}·qa_{n−1}
  - Sum over a window (e.g. 1–5 min) and normalise by average depth (qb + qa)/2.
- **Queue imbalance:** QI = (qb − qa)/(qb + qa) at the touch, sampled at decision time.
- **Trade-sign imbalance / CVD:** sign each trade with the Lee-Ready/tick rule (LTP ≥ ask → buy, ≤ bid → sell, else compare to mid). CVD = cumulative signed volume since open (or over a rolling window). Order imbalance = (buy vol − sell vol)/(buy vol + sell vol).
- **Total buy/sell pending quantity** (NSE broadcast "total buy qty / total sell qty"):
  - TBQ/TSQ ratio or (TBQ − TSQ)/(TBQ + TSQ).
  - Caveat: it includes orders far from the touch that can be cancelled cheaply, so it is noisier than touch-level imbalance (no Indian evidence found).
  - Changes in it are likely more informative than levels.

**Decay:** the NSE study (5-min strong, gone by 30 min) and US HFT evidence agree that order-flow features computed at 14:40–14:45 help time the entry and confirm direction for the next few minutes. They should *not* be expected to forecast a 1–4 day option hold. The most practical use in a MACD system:
1. Require MACD signal direction to agree with 5–15 min OFI/CVD sign at entry.
2. Use queue imbalance to choose limit vs market execution.

**For index options:** compute OFI on Nifty/BankNifty *futures* (the most liquid price-discovery instrument) and constituent heavyweights. Aggregate signed OFI across top constituents is an index-level flow feature (cross-asset lagged OFI per Cont-Cucuringu-Zhang).

**Daily-horizon carryover:** a daily cumulative imbalance (e.g. full-day CVD normalised by volume) may carry modest next-day information, via the imbalance autocorrelation channel in US data. Indian daily evidence was not found and must be tested.

### Gaps
- No Indian study found on: CVD or trade-sign imbalance at daily/next-day horizons for NSE stocks; NSE "total buy/sell quantity" as a predictor; OFI on Nifty/BankNifty futures or on index options.
- The Indian FRL paper's authors, sample period and effect sizes could not be retrieved (403). Only the abstract-level claims above are verified.
- The decay numbers for US OFI (exact R² by horizon) were not retrieved in this session.

---

## 4. Option-market-derived sentiment beyond PCR (IV skew, risk reversals, skew changes, unusual activity, option volume leading stock prices): evidence, including India

### Takeaway
US evidence that option-implied skew and signed option volume predict stock returns is strong:
- Xing-Zhang-Zhao skew.
- Pan-Poteshman signed put/call volume: >40 bp next day, >1% next week.
- Johnson-So O/S ratio.

Indian evidence is thinner but supportive for *stock options around events*: options skew and the put/call volume ratio predict the sign of the earnings surprise one day before the announcement. For *index options*, a 2022–23 Indian study found volume/OI ratios do not predict Nifty. Public (unsigned) PCR is much weaker than the signed, opening-buy measures used in the US studies.

### Cited Findings
- **Xing, Zhang & Zhao (2010):** IV skew = OTM put IV − ATM call IV. It has a significantly negative coefficient in Fama-MacBeth regressions and predicts stock returns for up to six months cross-sectionally. Interpretation: informed traders with bad news buy OTM puts. — [Fu, Arisoy, Shackleton, Umutlu, "Option-Implied Volatility Measures and Stock Return Predictability" (J. Derivatives), Lancaster eprint](https://eprints.lancs.ac.uk/id/eprint/80351/2/JoD_1_.pdf)
- **India skew study:** the level of option implied volatility skew has significant predictive ability for the future price of the underlying, using Nifty as a proxy, and "implied volatility smirks gave statistically significant predictability". Journal quality and sample are unclear. — [ResearchGate: Study of Implied Volatility Skew in Indian Stock Market](https://www.researchgate.net/publication/362302776_Study_of_Implied_Volatility_Skew_In_Indian_Stock_Market)
- **Saurav, Agarwalla & Varma (IIMA; American Business Review 27(2), Nov 2024), Indian stock options around earnings announcements:**
  - IV and options skew rise monotonically before the announcement day and fall after.
  - Options volume relative to spot and futures behaves the same way, so informed investors prefer options.
  - Options skew and put-to-call volume ratio predict the *sign* of the earnings surprise one day before the announcement.
  - — [IIMA publication page](https://iima.ac.in/publicationasymmetric-uncertainty-around-earnings-announcements-evidence-options-markets); [DOAJ record](https://doaj.org/article/979627e3a45d4146a4382cccbd381512)
- **Indian index-options study (Springer chapter, 2024; Jan 2022 – Jan 2023):** option volume and OI ratios that predict in individual stock options do *not* hold for index options. — [Springer: Option Volume and Open Interest for Predicting Underlying Return—Index Option in Indian Stock Market](https://link.springer.com/chapter/10.1007/978-981-97-6242-2_6)
- **Pan & Poteshman (US):** stocks with low put-call ratios beat high-ratio stocks by >40 bp next day and >1% over the next week. They used *opening buyer-initiated* option volume (not public PCR) and attribute predictability mainly to informed trading. — [Pan & Poteshman, "The Information in Option Volume for Future Stock Prices" (MIT)](https://www.mit.edu/~junpan/volume.pdf); [NBER w10925](https://www.nber.org/system/files/working_papers/w10925/w10925.pdf)
- **Johnson & So (2012):** option-to-stock volume ratio (O/S) negatively predicts stock returns, under information asymmetry and short-sale constraints. P/C ratios have more daily-level predictability than O/S. — [Osuva (Univ. of Vaasa) thesis summary](https://osuva.uwasa.fi/bitstreams/b2072745-12a4-42f8-912d-670191c1a32d/download); [ResearchGate: The Option to Stock Volume Ratio and Future Returns](https://www.researchgate.net/publication/277472327_The_Option_to_Stock_Volume_Ratio_and_Future_Returns)
- **Option volume imbalance has been studied as an equity-market return predictor** (arXiv 2201.09319). Not fetched. — [arXiv: Option Volume Imbalance as a predictor for equity market returns](https://arxiv.org/pdf/2201.09319)
- **Muravyev, Pearson & Pollet (JFE 2025), "Why does options market information predict stock returns?"** — the paper exists and directly addresses the mechanism. — [ScienceDirect](https://www.sciencedirect.com/science/article/pii/S0304405X25001618); [FMA working version](https://fmai.memberclicks.net/assets/docs/Derivatives2022/MuravyevPearsonPollet.pdf). See Gaps on its conclusions.
- **Informed trading shows up across the IV surface/term structure, and non-fundamental shocks also move skew:** a caution that skew changes are not purely informational. — [AUT ACFR: Informed Options Trading on the IV Surface](https://acfr.aut.ac.nz/__data/assets/pdf_file/0009/264591/Informed_Option_Trading_Term_20190414.pdf); [AUT ACFR: Non-fundamental Shocks and IV Skew](https://acfr.aut.ac.nz/__data/assets/pdf_file/0007/686752/5b-Asli-Eksi-B_NFShocks_and_IVSkew.pdf)

### Inferences
**Computable from the user's option-chain snapshots:**
1. **Skew:** IV(OTM put, about 0.25 delta or about 5% OTM) − IV(ATM call), per XZZ.
2. **Risk reversal:** RR25 = IV(25Δ call) − IV(25Δ put).
3. **Δskew:** intraday change since open, or day-over-day at the same time (e.g. 14:45 vs 14:45 previous day). Changes avoid the persistent structural put skew in Nifty.
4. **Call-put IV spread at matched strikes:** Cremers-Weinbaum-style put-call parity deviation, using same-strike call and put IVs.
5. **Volume-weighted P/C from chain volume:** P/C volume and ΔOI split by OTM/ATM.
6. **O/S ratio for stocks:** total option volume / underlying cash volume, relative to its own 20-day average, as an "unusual activity" flag.
7. **Signed option flow:** classify option trades using the recorded bid/ask on option ticks to approximate Pan-Poteshman "buyer-initiated" volume. Combine with ΔOI to separate opening from closing trades.

**Where evidence is best:** single-stock options before scheduled events (results). For Nifty/BankNifty index options, evidence that volume/OI-based sentiment predicts direction is weak or negative in India. Index skew is dominated by hedging demand, so it is better used as a regime/pricing feature.

**Unsigned public PCR** is a much weaker proxy than signed opening-buy volume. Expect much smaller effects than the US 40 bp/day.

**Stock-level skew signals** in the XZZ literature work over weeks to months in the cross-section. For a 1–4 day option hold, the most relevant signal is the *change* in skew/RR and abnormal signed option volume over the previous 1–2 days, especially pre-event.

### Gaps
- No Indian study found that tests skew *changes* or 25Δ risk reversals for next-day Nifty/BankNifty direction with out-of-sample statistics.
- Easley, O'Hara & Srinivas (1998, option volume leads stock prices) and Cremers & Weinbaum (2010, call-put IV spread predicts returns) were not verified in this session. They are canonical US references, but no Indian replication was found.
- Muravyev-Pearson-Pollet (JFE 2025) conclusions were not fetched. My recollection, unverified here, is that they attribute much of options-implied predictability to stock borrowing costs/short-sale frictions rather than private information. If correct, this predictability may be weaker for liquid Nifty F&O names, where shorting via futures is easy.
- The sample period and effect sizes in the IIMA earnings-options paper were not available from the abstract page.

---

## 5. News/social sentiment for Indian stocks, and event effects (results dates, ex-dates, index rebalances) on option buying

### Takeaway
Indian earnings announcements show significant pre- and post-event abnormal returns (drift), and the options market leads: IV, skew and option volume build up before results, and skew and P/C volume predict the surprise sign a day ahead. IV collapses after the announcement (vol crush), which hurts option buyers who hold through results. Little rigorous evidence was found on news/social-media sentiment for short-horizon NSE returns, or on ex-date/index-rebalance effects on options.

### Cited Findings
- **Indian stock options around earnings:** IV and options skew rise monotonically before the announcement and fall after. Skew and put/call volume predict surprise sign one day ahead. Options volume relative to spot/futures rises before announcements. — [IIMA: Asymmetric uncertainty around earnings announcements (Saurav, Agarwalla, Varma 2024)](https://iima.ac.in/publicationasymmetric-uncertainty-around-earnings-announcements-evidence-options-markets)
- **469 Indian companies, Dec 2002 – Dec 2011 (37 quarters):** significant pre-event abnormal returns in 32/37 quarters and significant post-event abnormal returns in 35/37 quarters, rejecting semi-strong efficiency. — [Vision (SAGE) 19(1) 2015, RePEc](https://ideas.repec.org/a/sae/vision/v19y2015i1p25-36.html)

### Inferences
**For option buyers:**
1. Avoid holding long options through results unless the expected move exceeds the implied move: IV rises pre-event and collapses after.
2. The pre-results window (1–3 days before) is when option-implied signals (skew change, P/C volume, O/S spike) carry the most documented information in India.
3. Post-results drift (documented 2002–2011) suggests directional option buys *after* the announcement (once IV has crushed) may be cheaper and still benefit from drift. This needs testing on recent data.

**Freely available event calendars:** NSE corporate announcements, the board-meeting/financial-results calendar, the corporate-actions (ex-date) list, and NSE Indices rebalance announcements. A practical feature set is days-to-results, days-since-results, an ex-dividend in the holding window, and index inclusion/exclusion effective dates.

**Ex-dividend:** ex-dates mechanically drop the stock price by the dividend. Call/put buyers in single-stock options should account for it. This is mechanical, not predictive.

### Gaps
- No peer-reviewed study found on news or social-media (Twitter/X, Moneycontrol, Telegram) sentiment predicting intraday-to-4-day returns for NSE stocks.
- No Indian study found on index-rebalance (Nifty 50 inclusion/exclusion) price effects or option activity around them in this session.
- No recent (post-2015) Indian post-earnings-announcement-drift study was retrieved. The 2002–2011 evidence may be dated.

---

## 6. Time-of-day, day-of-week and expiry-day effects in NSE intraday returns and option premiums (with post-2024 SEBI structural changes)

### Takeaway
NSE intraday volatility is reverse-J shaped (high at the open, falling, rising into the close), with extra volatility after weekends and holidays. Expiry days raise volume and volatility, especially in the last half hour (settlement-price window). The expiry calendar changed sharply in 2024–25, so pre-2025 expiry-day studies need re-dating:
- Only one weekly index expiry per exchange from 20 Nov 2024 (Nifty only on NSE; BankNifty monthly only).
- Expiry moved from Thursday to Tuesday from 1 Sep 2025.
- Intraday position-limit monitoring and delta-based (FutEq) OI introduced in 2025.
- F&O pre-open from 8 Dec 2025.

### Cited Findings
- **Reverse-J intraday volatility on NSE,** with much higher intraday variation than in other markets. Higher volatility in the first hour after weekends, the first half hour after holidays, and the last hour before weekends. — [IIMA working paper](https://www.iima.ac.in/sites/default/files/rnpfiles/19191060542012-11-03.pdf) (via search summary)
- **Expiration-day effects (Agarwalla & Pandey, J. Futures Markets 2013):** volatility of stocks with derivatives rises in the last half hour of expiry day (the settlement-price window) but not in other intervals. — [Wiley: Expiration-Day Effects and Short Trading Breaks… Indian Market](https://onlinelibrary.wiley.com/doi/10.1002/fut.21632)
- **Expiry days and expiry weeks** have significantly higher volume, and expiry affects index returns and volatility. — [ResearchGate: Expiry day impact on return on NSE](https://www.researchgate.net/publication/339949132_Expiry_day_Impact_on_return_on_Indian_Stock_market_NSE-_an_Empirical_Study); [AMH: Expiration Day Effects of Index Futures](https://ojs.amhinternational.com/index.php/jebs/article/download/210/210)
- **Weekly expiry days** raise intraday volatility, especially in the last hours, linked to option hedging, gamma and unwinding of large positions (Nifty and BankNifty study). — [Zenodo: Impact of Weekly Options Expiry on Market Volatility](https://zenodo.org/records/19220278)
- **22-year analysis** of F&O expiry timing vs first-day SIPs on Nifty 50 (expiry-timing return differences). — [arXiv 2507.04859](https://arxiv.org/html/2507.04859v2)
- **SEBI F&O package (1 Oct 2024 circular):**
  - One weekly index expiry per exchange, effective for contracts introduced after 20 Nov 2024. NSE kept only Nifty weekly; BSE kept Sensex weekly, and Sensex 50/Bankex weeklies were discontinued.
  - Minimum contract size raised from Rs 5 lakh to Rs 15 lakh.
  - Upfront collection of option premium from buyers.
  - Intraday monitoring of position limits.
  - Removal of calendar-spread margin benefit on expiry day.
  - Higher margins (extra ELM) near expiry.
  - — [Business Standard: Sebi rolls out 6 measures](https://www.business-standard.com/amp/markets/news/sebi-announces-six-key-changes-to-curb-speculation-in-derivatives-trading-124100101316_1.html); [Business Today: BSE discontinues weekly Sensex 50, Bankex](https://www.businesstoday.in/amp/markets/story/after-sebis-new-fo-rules-bse-discontinues-weekly-index-derivatives-contracts-on-sensex-50-bankex-448626-2024-10-03); [Share.Market: weekly expiry days](https://www.share.market/buzz/insights/weekly-expiry-days-in-indian-fo-markets/)
- **Expiry day moved Thursday → Tuesday** (NSE circular NSE/FAOP/68747, 25 Jun 2025): contracts expiring on or after 1 Sep 2025 expire on Tuesday, with monthly contracts on the last Tuesday. First Tuesday Nifty weekly expiry was 2 Sep 2025. BSE expiries are Thursday. — [HDFC Securities: Revision of NSE & BSE expiry day](https://www.hdfcsec.com/blog/details/revision-of-nse-bse-expiry-day-of-derivatives-contracts); [TradingQnA: SEBI confirms Tuesday NSE / Thursday BSE](https://tradingqna.com/t/sebi-confirms-derivatives-expiries-tuesday-for-nse-thursday-for-bse/183366)
- **2025 SEBI equity-derivatives framework:**
  - Delta-based "Future Equivalent OI" (FutEq OI) for OI/MWPL measurement.
  - Higher index position limits.
  - Intraday index-option limits from 1 Oct 2025: net Rs 5,000 cr futures-equivalent per entity; gross Rs 10,000 cr each side.
  - Exchanges monitor with at least four random intraday snapshots, one between 14:45 and 15:30.
  - Phased July–Dec 2025.
  - — [Business Standard: Sebi tightens intraday position limits (Sep 2025)](https://www.business-standard.com/amp/markets/news/sebi-stricter-intraday-position-limits-options-market-regulation-125090200971_1.html); [Probe42: Equity Derivatives Framework 2025](https://resources.probe42.in/regulatory-updates/sebi-updates/equity-derivatives-framework-2025/)
- **Pre-open session in F&O (NSE and BSE) from 8 Dec 2025** (futures; next-month futures included in the last 5 days before expiry). — [Fyers notice](https://fyers.in/notice-board/introduction-of-pre-open-session-in-equity-derivatives-fo/); [Morung Express/PTI: BSE pre-market F&O from Dec 8](https://morungexpress.com/bse-to-introduce-pre-market-trading-in-index-and-stock-fo-from-dec-8)

### Inferences
**Regime breaks for backtests:**
- BankNifty weekly expiry (Wednesday) ended Nov 2024, so post-Nov-2024 BankNifty has only monthly expiry.
- Nifty expiry day is Thursday before Sep 2025 and Tuesday from 2 Sep 2025.
- Any day-of-week or expiry-day feature must be coded as "days to expiry" / "is expiry day", not as a weekday.
- Weekday effects learned pre-Sep 2025 (e.g. "Thursday is volatile") transfer to Tuesday only through the expiry channel.

**The user's 14:45 decision time sits inside:**
1. The highest-volatility late-session window.
2. SEBI's mandated position-limit snapshot window (14:45–15:30).
3. On expiry days, just before the 15:00–15:30 settlement-price averaging window, where volatility spikes are documented.

This argues for a separate expiry-day model or excluding expiry-day entries for 1–4 day holds in weekly options (theta and gamma dominate).

**Day-of-week/weekend:** extra volatility after weekends and holidays, plus weekend theta decay for long options bought on Friday afternoon. A "holding over weekend/holiday" feature is relevant for option buyers.

**Since Dec 2025, F&O has a pre-open session.** The opening print for futures is now an auction price. Gap-at-open features computed from futures should account for this break.

### Gaps
- No post-Sep-2025 study found on Tuesday-expiry intraday return/volatility patterns for Nifty, or on whether BankNifty behaviour changed after its weekly expiry was removed.
- No rigorous Indian study found on time-of-day *directional* return patterns (e.g. last-hour drift, overnight vs intraday return split) for Nifty in the post-2024 regime. NY Fed "overnight drift" work exists for the US but was not tested for India in this session.
- No study found quantifying option-premium (theta/IV) intraday patterns on expiry day under the new regime.
