# Volatility and Price-Location Features for Long-Option (CE/PE) Buying

Research notes compiled 3 Oct 2026. Scope: what makes buying calls/puts pay, and which entry-time features (IV vs RV, VRP, India VIX, sigma move, MA/Bollinger distance, expiry-series regime) can pick symbol, side and timing for an NSE intraday-to-4-session MACD buyer with a 40% stop and trailing exit. Under each question the sections are: Takeaway, Cited Findings, Inferences, Gaps. Every cited item has a link. Unsourced reasoning appears only under Inferences.

## Q0. What drives long-option returns mechanically, and how much do option buyers lose on average?

### Takeaway
On average, buying options loses money. Delta-neutral long-volatility positions lose about 3% per week on S&P 500 straddles, and delta-hedged index options lose about 0.7% per day. Implied volatility usually sits above the volatility that is later realized, so the buyer's gamma gains fall short of theta. In India, more than 91% of individual F&O traders lost money in FY25. One detail matters for an intraday buyer: the index-option risk premium is earned almost entirely **overnight**. Open-to-close option returns are roughly flat to positive in the US and only weakly negative (not significant) in India.

### Cited Findings
- Zero-beta, at-the-money S&P 500 straddles lose about **3% per week** on average. Coval & Shumway (JF 2001) take this as evidence that systematic volatility risk is priced and that option buyers pay a premium for it. — [Coval & Shumway summary](https://academicnewsletter.sufe.edu.cn/info/361566); [Deep Blue record](https://deepblue.lib.umich.edu/items/455dbe73-b56b-44f3-841f-c7751deffc7a/full)
- Also from Coval & Shumway: in theory, expected call returns exceed the underlying's return and rise with strike, while expected put returns are below the risk-free rate. In other words, unhedged OTM calls carry high but very noisy expected returns, and puts carry negative expected returns because they act as insurance. — [Coval & Shumway summary](https://academicnewsletter.sufe.edu.cn/info/361566)
- Bakshi & Kapadia (RFS 2003), "Delta-hedged gains and the negative market volatility risk premium": delta-hedged long S&P 500 options lose money on average, which implies a negative market volatility risk premium. — [Bakshi & Kapadia, RFS 16(2)](https://doi.org/10.1093/rfs/16.2.527)
- Muravyev & Ni (JFE 2020) split this loss by time of day. Delta-hedged S&P 500 index options average **−0.7% per day**: close-to-open is **−1% per day** and open-to-close is **+0.3% per day**. The pattern holds across maturities, moneyness and equity options. The authors' explanation is that option prices do not reflect how much higher volatility is intraday than overnight. — [IDEAS/RePEc JFE 136(1)](https://ideas.repec.org/a/eee/jfinec/v136y2020i1p219-238.html); [CXO summary](https://www.cxoadvisory.com/equity-options/intraday-versus-overnight-option-returns/)
- **India.** Bhat, Pandey & Nageswara Rao (Journal of Futures Markets, 2024) study Nifty options from 2017 to 2020. For delta-hedged **short** Nifty options, overnight returns are positive and highly significant, while intraday returns are mostly negative and not significant, and so are full-day returns. The pattern holds for calls and puts, across moneyness and maturities, and after controlling for weekends. The day–night gap narrows considerably on days with large index jumps. Their conclusion is that the VRP pays sellers for overnight risk only. — [vLex abstract, JFM 2024](https://law-journals-books.vlex.com/vid/the-asymmetry-in-day-1115554990)
- **India.** A study of Nifty and India VIX daily data from April 2021 to July 2025 finds a consistently positive and statistically significant VRP: implied variance persistently exceeds realized variance. Short VIX futures, synthetic short variance swaps and daily delta-hedged short ATM straddles all made money in normal regimes, with losses concentrated in volatility shocks. (This comes from a search snippet; I could not confirm the paper's title or authors.) — [Search-result summary; underlying paper not verified](https://dspace.uohyd.ac.in/handle/1/4779)
- **India.** An older University of Hyderabad study examines how implied and realized volatility relate for Nifty index options. — [UoH repository](https://dspace.uohyd.ac.in/handle/1/4779)
- **India.** A study of Nifty straddles finds negative Sharpe ratios for straddle strategies across regimes. India VIX explains little of their performance, with a weak positive effect on long straddles and a weak negative effect on short straddles. — [Univ. of Vaasa thesis repository](https://osuva.uwasa.fi/server/api/core/bitstreams/8a34262d-fdad-4af1-8f84-34f088e86a3a/content)
- **India.** A separate study finds that volatility demand in Nifty option order flow significantly affects changes in the variance risk premium. — [Univ. of Vaasa entry](https://sites.uwasa.fi/mfjuva/?p=2597)
- **SEBI, FY25.** More than 91% of individual equity-derivatives traders lost money. Net losses after costs were Rs 1,05,603 crore, up 41% from Rs 74,812 crore in FY24. Unique individual traders fell from 61.4 lakh in Q1 FY25 to 42.7 lakh in Q4. The study covered the top 13 brokers, about 96 lakh traders. — [Business Standard](https://www.business-standard.com/markets/news/net-losses-of-traders-in-fo-widens-in-fy25-sebi-study-125070701221_1.html); [Moneylife](https://moneylife.in/article/91-percentage-of-retail-traders-lost-money-in-derivatives-losses-in-fo-surged-41-percentage-to-rs105-lakh-crore-in-fy2425-sebi-study/77613.html)
- **Government reply, FY25-26.** Retail F&O traders lost Rs 91,685 crore despite the SEBI curbs, with an average loss of Rs 1.17 lakh (headline only; full figures not checked). — [Moneylife](https://www.moneylife.in/article/retail-fo-traders-lose-91685-crore-in-fy2526-despite-sebi-curbs-average-loss-hits-117-lakh-govt/81318.html)
- **Theta on weekly Nifty options (practitioner estimate).** An ATM weekly Nifty option with 3–4 days left has theta of roughly −8 to −12 points per share. That is about Rs 650 per lot per day at a 65-unit lot if spot and IV are unchanged. A weekly option can lose 40–60% of its premium to time alone if the index does not move. — [OneTradeJournal (practitioner)](https://onetradejournal.com/glossary/theta-decay); [OneTradeJournal, buyer mistakes](https://onetradejournal.com/learn/top-mistakes-options-buyers-make)

### Inferences
- **Standard Taylor decomposition (textbook, not from a cited source).** Option P&L over a short interval is approximately Δ·dS + ½Γ·dS² + Θ·dt + Vega·dσ. For a delta-hedged long option, gamma minus theta comes to about ½ΓS²(σ_realized² − σ_implied²)·dt. A long option therefore gains volatility P&L only when realized variance over the holding window exceeds implied variance. An unhedged buyer, like this user, also has a directional (delta) bet layered on top. For short holds, delta P&L dominates as long as the move is large relative to the premium. Theta and vega crush dominate when the move is small.
- **Holding period.** The cited US and India evidence puts the buyer's structural disadvantage mainly in overnight holds. Intraday holds pay little or none of the VRP. For a strategy that holds 0–4 sessions, every overnight is a structural cost (gaps aside). That points to features favoring same-day exits unless trend or regime evidence justifies carrying. Bhat et al. note the overnight premium shrinks on jump days, so carrying after a large-move day is relatively less penalized.
- **Stop-loss interaction.** A 40% premium stop on a short-dated ATM option can be hit by theta plus a small adverse move. Entry-time features need to lift the odds that realized movement beats implied movement soon after entry.

### Gaps
- I did not find a published, peer-reviewed estimate of average **unhedged** long-call or long-put returns for Nifty by moneyness and days to expiry. The India evidence is for delta-hedged or short positions.
- I could not verify the 2021–2025 Nifty VRP paper (title, authors, VRP size in vol points). Its findings come from a search snippet.
- I found no India-specific study of single-stock (NSE stock option) VRP or delta-hedged returns.

## Q1. What numbers do studies give for option-return predictability from the IV–RV spread / VRP, and is there anything for NSE/Nifty or India VIX?

### Takeaway
In US single-stock options the cross-sectional effect is very large. Goyal & Saretto (JFE 2009) sort stocks on log(HV/IV). Buying straddles where historical vol is well above implied and selling where it is well below earns about **22.7% per month** before costs. Related sorts give large spreads too: Vasquez's IV term-structure slope sort earns **19.6% per month** in straddles. So does Cao & Han's idiosyncratic-vol sort, where options on high-idio-vol stocks are overpriced. For Nifty and India VIX, I found evidence that the VRP is positive. I found no published cross-sectional IV–RV sort for NSE stock options.

### Cited Findings
- **Goyal & Saretto (JFE 2009).** Stocks are sorted into deciles on the difference between historical realized vol and ATM IV, and the straddle and delta-hedged call and put portfolios are rebalanced monthly. The zero-cost straddle strategy, long high HV−IV and short low HV−IV, earns a **statistically significant 22.7% per month**. The result holds across market conditions, risk characteristics, industries and option liquidity, and standard factor models do not explain it. The rationale is that IV mean-reverts toward HV. — [ERIM event page](https://erim.eur.nl/research/events/detail/1124); [SUFE summary](https://academicnewsletter.sufe.edu.cn/info/357238); [BSIC write-up](https://bsic.it/dislocations-historical-implied-volatility-indicators-volatility-trades/)
- **Vasquez (JFQA 2017), "Equity volatility term structures and the cross-section of option returns."** The slope of the IV term structure is positively related to future option returns. Buying straddles in the top slope decile and selling the bottom decile earns **19.6% per month** before costs. The top decile is upward sloping: short-dated IV is low relative to long-dated IV. — [Alpha Architect summary](https://alphaarchitect.com/2014/01/equity-term-structure-and-option-returns/)
- **Cao & Han (JFE 2013).** Delta-hedged equity option returns **decline monotonically with the underlying's idiosyncratic volatility**, and standard risk factors and known anomalies do not explain it. Dealers charge more for options on high-idio-vol stocks because those are costlier to hedge and arbitrage. Limits-to-arbitrage proxies explain about 40% of the effect. — [OptionMetrics research page](https://optionmetrics.com/research/j-cao-and-b-han-cross-section-of-option-returns-and-idiosyncratic-stock-volatility/); [SUFE summary](https://academicnewsletter.sufe.edu.cn/info/356714)
- **India.** India VIX has systematically overstated subsequent realized Nifty volatility on average, which confirms an index VRP. — [Search-result synthesis of Indian studies, incl. IIM Indore thesis](https://iimidr.ac.in/wp-content/uploads/2020/01/Three-essays-on-model.pdf)
- **India.** India VIX shows only weak explanatory power for Nifty long and short straddle P&L in one regime study. — [Univ. of Vaasa repository](https://osuva.uwasa.fi/server/api/core/bitstreams/8a34262d-fdad-4af1-8f84-34f088e86a3a/content)

### Inferences
- **Most transferable feature for choosing a stock option.** Use the ratio of recent realized vol to the option's IV, for example 10- or 20-day close-to-close or Parkinson vol against ATM IV. Prefer buying where RV/IV is high, meaning the market is underpricing movement. Avoid buying where IV is well above RV, such as pre-event or after a vol spike that has since calmed.
- **Secondary filters.** Avoid the most "lottery-like" names, those with the highest idiosyncratic vol, where premiums are richest per Cao & Han. Favor an upward-sloping near-term term structure: near-week IV below next-month IV, following Vasquez.
- **Holding-period caveat.** These are monthly, delta-hedged or straddle results. For directional holds of 0–4 days the effect works mainly through lower theta and vega drag, not delta. Expect a much smaller edge per trade, though it should still help the 40% stop survive.
- **IV rank / IV percentile.** This is a time-series cousin of the HV−IV sort. No study I found tested IV rank directly on long-option returns. Using it is reasoned by analogy, not proven.

### Gaps
- No peer-reviewed cross-sectional HV−IV or VRP sort for **NSE single-stock options** turned up. A local backtest would be needed.
- No quantified study found of whether **India VIX level or daily change** predicts long Nifty option returns, apart from the weak straddle regression above.
- No study found testing **IV rank / IV percentile** as a predictor of long-option P&L in any market.

## Q2. Do large "sigma moves" intraday continue or revert, at what horizons, in index vs single stocks? Is there evidence for India?

### Takeaway
The evidence splits by time of day and horizon:
- **Continuation within the day.** The market's first half-hour return, and the rest-of-day return, predict the last half-hour return in the same direction. This holds for SPY, 60+ futures and most developed markets, and is stronger on high-volatility, high-volume and news days. Continuation is linked to dealer short-gamma hedging. A volatility-scaled breakout from a "noise area", with a trailing stop, earned a Sharpe of about 1.3 on SPY.
- **Reversal of large opening moves.** Large moves at the open of index futures tend to partly reverse intraday, but the edge mostly disappears after costs.
- **Reversal across days.** Intraday momentum reverts over the next few days.

I found no peer-reviewed India test of intraday momentum.

### Cited Findings
- **Gao, Han, Li & Zhou (JFE 2018), "Market intraday momentum."** SPY data from 1993 to 2013. The first half-hour return, measured from the prior close, predicts the last half-hour return. The effect is stronger on more volatile days, higher-volume days, recession days and macro-news days, and it also appears in 10 other heavily traded ETFs. — [WUSTL profile](https://profiles.wustl.edu/en/publications/market-intraday-momentum/); [Alpha Architect](https://alphaarchitect.com/2014/08/attention-prop-traders-the-first-half-hour-of-trading-predicts-the-last-half-hour/)
- **Earlier version of the same study.** Data from 1999 to 2012. A simple strategy timed off the first half-hour return earned **6.34% per year**. — [Alpha Architect](https://alphaarchitect.com/2014/08/attention-prop-traders-the-first-half-hour-of-trading-predicts-the-last-half-hour/)
- **International markets.** The first half-hour return significantly predicts the last half-hour return in **12 of 16 developed markets**. — [Univ. of Reading accepted version](https://reading-9.eprints-hosting.org/95566/1/Accepted-Version.pdf)
- **Baltussen, Da, Lammers & Martens (JFE 2021).** More than 60 futures across equities, bonds, commodities and FX, 1974–2020. The rest-of-day return predicts the last 30 minutes everywhere. This is linked to **gamma hedging by option market makers and leveraged ETFs**: hedging short gamma forces trading in the direction of the move. The predictability **reverts over the next days**. — [EUR Pure](https://pure.eur.nl/en/publications/hedging-demand-and-market-intraday-momentum/); [paper PDF](https://www3.nd.edu/~zda/intramom.pdf); [Alpha Architect](https://alphaarchitect.com/hot-topic-does-gamma-hedging-actually-affect-stock-prices/)
- **Zarattini, Aziz & Barbon (Swiss Finance Institute RP 24-97, 2024), "Beat the Market."** The strategy enters SPY trend-following positions as soon as intraday price leaves a volatility-defined "noise area", rather than waiting for the last 30 minutes, and uses **dynamic trailing stops**. From 2007 to early 2024 it returned 1,985% net of costs, **19.6% per year, Sharpe 1.33**. The authors also test it across volatility regimes, dealer gamma imbalance and day of week. It placed 4th in the Quantpedia Awards 2025. — [SFI](https://www.sfi.ch/de/publications/n-24-97-beat-the-market-an-effective-intraday-momentum-strategy-for-s-p500-etf-spy); [Concretum](https://concretumgroup.com/beat-the-market-an-effective-intraday-momentum-strategy-for-sp500-etf-spy/); [UniSG news](https://fsi.unisg.ch/de/newsdetail/news/quantpedia-awards-2025-fsis-barbon-and-team-secure-4th-place-with-intraday-momentum-strategy/)
- **Grant, Wolf & Yu.** S&P 500 futures, November 1987 to September 2002. Highly significant **intraday reversals after large opening price changes**. The effect is stronger after large **positive** opens. Reversal significance drops sharply once a bid–ask cost proxy is subtracted. — [Montclair repository](https://researchwith.montclair.edu/en/publications/intraday-price-reversals-in-the-us-stock-index-futures-market-a-1/)
- **Fung, Mok & Lam.** S&P 500 and Hang Seng futures. Intraday reversals follow large opening moves, and the size of the reversal rises with the size of the initial move. — [HKBU scholars](https://scholars.hkbu.edu.hk/en/publications/intraday-price-reversals-for-index-futures-in-the-us-and-hong-kon/)
- **Emerging markets (Indonesia).** The first 30-minute return significantly and positively predicts the last 30-minute return for both winner and loser stocks. — [UMS eprints](https://eprints.ums.edu.my/id/eprint/42874)
- **Muravyev & Ni.** Option prices underweight the fact that intraday volatility is much higher than overnight volatility. — [IDEAS](https://ideas.repec.org/a/eee/jfinec/v136y2020i1p219-238.html)

### Inferences
- **Proposed sigma-move rule (needs a local test).** Measure today's move from the prior close or the open in units of the expected daily move, implied (IV/√252 × spot) or realized (ATR or average absolute daily move). The cited evidence suggests:
  - (a) A large move that persists **after** the opening auction or first 30 minutes favors continuation into the close, especially on high-volume or news days and for indices where dealer gamma hedging matters.
  - (b) A large **gap or opening spike**, measured at the open itself, is more likely to partly reverse.
  - (c) Intraday momentum gains tend to give back over the next 1–3 days, which argues against carrying a momentum entry multi-day unless the expiry-series regime is trending (Q4).
- **Fit with this user's design.** The Zarattini et al. design (volatility-band breakout plus trailing stop) is structurally close to a MACD entry with a trailing exit. Their "noise area" is in effect a sigma-move filter. Requiring the move from the open to exceed k× the average recent intraday move before taking a MACD signal is a directly testable feature.
- **Fit with prior local research.** This matches the user's earlier lane study from 2 Oct (local memory): tight exits lost and loose exits were regime-dependent. Momentum strategies need room for noise.
- **Single stocks vs index.** I found no source contrasting the two at intraday horizons. The gamma-hedging mechanism points to stronger continuation in indices and heavily optioned large caps.

### Gaps
- No peer-reviewed **India/NSE** study of the market intraday momentum effect for Nifty or Bank Nifty turned up in searches.
- No source quantified continuation vs reversal **conditional on move size in IV-sigma units**. The Zarattini paper's exact noise-area formula could not be extracted from the summary page.
- No study found on India dealer gamma positioning and intraday trend days.

## Q3. Is distance from the 20-period MA (stretched spot or premium) predictive of continuation or reversal at short horizons?

### Takeaway
I found no rigorous academic evidence that 20-period MA z-scores or Bollinger %B predict short-horizon returns in index or Indian markets. Search results were practitioner write-ups describing Bollinger mean-reversion setups without performance statistics. The nearest academic evidence points in two directions:
- **Continuation:** intraday momentum, plus volatility-band breakouts with trailing stops on SPY.
- **Reversal:** large opening moves, and next-day reversal of intraday momentum.

So "stretched" is not a reliable reversal signal on its own. Its meaning depends on time of day and regime.

### Cited Findings
- Search results on Bollinger and MA-distance returned practitioner guides (e.g., a 20-period MA with ±2σ bands treated as overbought/oversold) and TradingView scripts, not peer-reviewed tests. — [PL India blog](https://www.plindia.com/blogs/mean-reversion-trading-bollinger-bands/); [LuxAlgo library](https://www.luxalgo.com/library/indicator/RDg014a9-bollinger-band-and-moving-average-v0-1-by/)
- One arXiv paper switches between momentum and mean-reversion using the Hurst exponent, framing "stretched" as regime-dependent. — [arXiv 2205.11122](https://arxiv.org/pdf/2205.11122)
- Breakouts beyond volatility bands followed with a trailing stop were profitable on SPY: Sharpe 1.33 from 2007 to 2024. This is effectively the opposite of fading band touches. — [SFI RP 24-97](https://www.sfi.ch/de/publications/n-24-97-beat-the-market-an-effective-intraday-momentum-strategy-for-s-p500-etf-spy)
- Large opening moves in index futures reverse intraday, though this is weak after costs. — [Montclair](https://researchwith.montclair.edu/en/publications/intraday-price-reversals-in-the-us-stock-index-futures-market-a-1/); [HKBU](https://scholars.hkbu.edu.hk/en/publications/intraday-price-reversals-for-index-futures-in-the-us-and-hong-kon/)
- A recent study attributes market time-series reversal mainly to investor overreaction rather than to a shortage of liquidity provision. — [PMC article](https://pmc.ncbi.nlm.nih.gov/articles/PMC9759686)

### Inferences
- **Spot distance from the 20-period MA.** For a MACD buyer the most defensible use is as a **regime-conditioned** feature, to be tested:
  - In a trending expiry series (high efficiency ratio, Q4), a moderate stretch (z of about 1–2) in the signal direction is consistent with momentum.
  - In a bracketing/range series, entries at z > 2 are more likely to mean-revert and hit the 40% stop.
  - Without such conditioning, expect little standalone predictive power.
- **Option premium distance from its own 20-period MA.** This mixes spot moves with IV changes and theta. A premium far above its MA usually means IV has expanded (vega already paid) or delta has jumped. It is mainly a "you are paying up" warning, not a direction signal. I found no study on it, so treat it as a hypothesis.

### Gaps
- No peer-reviewed short-horizon test of 20-period MA z-score or Bollinger %B predictability for Nifty, Indian stocks or US indices was found.
- No study on option-premium technical stretch.

## Q4. How do trend vs range (bracketing) regimes and expiry-cycle effects (days to expiry, position in series, expiry week) affect option-buyer returns in India after the 2024–2025 SEBI changes?

### Takeaway
SEBI's measures changed the expiry structure:
- **From 20 Nov 2024:** one weekly index expiry per exchange, larger index lot sizes, and extra tail-risk margin on expiry day.
- **Contracts expiring on or after 1 Sep 2025:** NSE expiries moved from Thursday to **Tuesday**, and Nifty 50 is NSE's only weekly.

Contract counts fell sharply but premium turnover barely moved. Individual-trader losses still **rose** in FY25. I found no academic study of buyer returns by days to expiry or by position in the series after these changes, and no peer-reviewed evidence on ADX, efficiency ratio or market-profile balance vs trend days for option buying. The trend-day logic rests on the intraday momentum literature (Q2) and the VRP mechanics (Q0).

### Cited Findings
- **SEBI circular, October 2024 (effective 20 Nov 2024).** Each exchange may offer weekly expiries on only **one benchmark index**. The rationale was excessive speculation and sharp price swings from concentrated short-tenor trading on expiry days. — [Share.Market explainer](https://www.share.market/buzz/insights/weekly-expiry-days-in-indian-fo-markets/); [ICICI Direct](https://www.icicidirect.com/research/equity/finace/sebi-clears-expiry-day-clash-between-nse-and-bse)
- **Measures effective 20 Nov 2024.** Larger index-derivative contract sizes, rationalized weekly index products, and higher tail-risk coverage on expiry day. — [Outlook Business](https://www.outlookbusiness.com/markets/sebis-regulatory-changes-have-minimal-impact-on-option-premium-turnover)
- **NSE moves to Tuesday.** NSE received SEBI approval in June 2025. Contracts expiring on or before 31 Aug 2025 stayed on Thursday; contracts expiring on or after **1 Sep 2025 moved to Tuesday**. A late-May 2025 SEBI circular required exchanges to choose Tuesday or Thursday. NSE kept weekly options only on Nifty 50. BSE uses Thursday. — [Business Today](https://www.businesstoday.in/markets/stocks/story/nse-receives-sebi-nod-for-tuesday-expiry-says-report-480736-2025-06-17); [Share.Market](https://www.share.market/buzz/insights/weekly-expiry-days-in-indian-fo-markets/)
- **First week of December 2024.** Weekly expiries fell from six to two (Nifty Thursday, Sensex Friday). NSE index option contracts fell **38%** to 1,658 million. Aggregate premium turnover showed no major change. — [Outlook Business](https://www.outlookbusiness.com/markets/sebis-regulatory-changes-have-minimal-impact-on-option-premium-turnover)
- **FY25 premium turnover.** Index option premium turnover dipped for the first time in eight years, to Rs 136 trillion from Rs 138 trillion. Monthly average premium turnover was Rs 9.1 trillion from December 2024 to March 2025. — [Business Standard](https://www.business-standard.com/amp/markets/news/index-option-annual-premium-turnover-sees-first-dip-in-8-years-in-fy25-125042801097_1.html)
- **After the July 2025 Jane Street ban.** Expiry-day derivatives premiums were about 40% below the year's average for such days, and turnover hit a four-month low. — [Business Today](https://www.businesstoday.in/markets/stocks/story/options-trading-india-falls-jane-street-ban-sebi-crackdown-484189-2025-07-11); [Angel One](https://www.angelone.in/news/market-updates/sebi-reports-drop-in-derivatives-notional-volumes)
- **Trader count and losses.** Unique F&O traders fell 20% to 6.7 million in December 2024 to May 2025 versus a year earlier, yet FY25 individual losses widened 41%. — [Business Standard](https://www.business-standard.com/amp/markets/news/individual-investors-shrink-20-percent-fo-segment-125070800801_1.html); [Business Standard](https://www.business-standard.com/markets/news/net-losses-of-traders-in-fo-widens-in-fy25-sebi-study-125070701221_1.html)
- **Weekly-option decay (practitioner).** Decay accelerates in the last 2–3 days before a weekly expiry and is heaviest on expiry day for ATM strikes. — [OneTradeJournal](https://onetradejournal.com/glossary/theta-decay)
- **Nifty day–night asymmetry.** Robust across times to expiration (2017–2020, the pre-change weekly regime). — [JFM 2024 abstract](https://law-journals-books.vlex.com/vid/the-asymmetry-in-day-1115554990)
- **Intraday momentum and trend days.** Momentum is stronger on high-volatility, high-volume and macro-news days. Gamma-hedging flows cause trend continuation into the close. — [WUSTL](https://profiles.wustl.edu/en/publications/market-intraday-momentum/); [EUR Pure](https://pure.eur.nl/en/publications/hedging-demand-and-market-intraday-momentum/)

### Inferences
- **Expiry calendar after September 2025.** For Nifty weeklies the high-theta days are now Friday to Tuesday, with the weekend inside the decay window. For stock options, NSE monthlies expire on the last Tuesday. A buyer entering Thursday or Friday with a 1–4 session hold pays two calendar days of weekend decay plus the overnight VRP (Q0). Prefer entries early in the weekly cycle (Wednesday) for multi-day holds, or next-week or monthly contracts when holding through the weekend.
- **Expiry-day (0-DTE) buying.** The 40% stop and theta make this a pure gamma bet. It pays only on trend days, where realized intraday movement far exceeds implied. The intraday-momentum evidence suggests confirming a trend day before buying, e.g., a first-hour sigma move beyond the noise band with volume above normal.
- **Regime features (efficiency ratio, ADX, market profile).** Using these over the current expiry series is economically sensible but unproven in the literature. For buyers:
  - A **trending series** means realized vol is concentrated in one direction, so delta P&L covers theta.
  - A **bracketing series**, where price oscillates inside the initial balance or value area, is the environment where realized vol falls below implied and sellers win.
  - Suggested test: Kaufman efficiency ratio (|net change| / sum of |changes|) over the series to date, with ER above about 0.3–0.4 classed as trending and below about 0.2 as bracketing. Combine with a check of whether today's price is outside the prior session's value area. Report buyer P&L by bucket.
- **Losses after the reforms.** FY25 losses rose despite fewer contracts and traders. The reforms changed the contract mix but did not remove the structural buyer disadvantage, so the features above matter more than the calendar changes.

### Gaps
- No academic or SEBI study was found measuring **option-buyer returns by days to expiry or by position within the expiry series** for Nifty or NSE stock options **after** the November 2024 or September 2025 (Tuesday) changes.
- No peer-reviewed evidence was found on **ADX, efficiency ratio or market-profile balance vs trend days** as predictors of option-buying returns in any market. Market-profile literature (Steidlmayer/Dalton) was not retrieved within the search budget.
- No India study was found linking dealer gamma positioning or expiry pinning to Nifty trend vs range days after the reforms.
