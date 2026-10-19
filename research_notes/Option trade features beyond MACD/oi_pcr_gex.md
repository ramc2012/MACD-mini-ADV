# Open Interest, PCR, Max Pain, OI Walls and Dealer Gamma (GEX) as Predictors for NSE Option Buyers

Research notes compiled 3 Oct 2026. Scope: OI, OI change / buildup, PCR (OI and volume), max pain and expiry pinning, OI walls, GEX / gamma flip / vanna-charm. Target user: buys NSE index options (Nifty/BankNifty) and stock options, holding intraday to 4 sessions, with a MACD-based entry system.

Source-quality note: several primary PDFs (Baltussen et al. JFE 2021; SqueezeMetrics GEX white paper) could not be text-extracted in this session, so their exact coefficients and t-stats are not reported here. Their findings are taken from the abstracts and secondary summaries cited below. Vendor blogs (FlashAlpha, SpotGamma, Indian broker or "glossary" sites) are flagged as such.

---

## Q1. What do academic studies find on OI/PCR predictability (Pan & Poteshman, Ni/Pearson/Poteshman pinning, Nifty/BankNifty PCR, OI, max pain)? Does it survive in India, and is it intraday or multi-day?

### Takeaway
The strong US results come from **non-public, signed, buy-to-open stock-option volume**, not from the publicly visible PCR. Low-PCR stocks beat high-PCR stocks by more than 40 bp the next day and about 1% over the next week. Expiry pinning (about 16.5 bp of return distortion per expiry) and a max-pain long-short effect (weekly horizon, mostly small and illiquid stocks) are real but small. The Indian evidence on Nifty/BankNifty PCR and OI is thin, low-quality, or weak. One frequency-domain study finds volume-PCR informative at about 2.5-day cycles and OI-PCR at about 12-day cycles. Nothing robust shows that public PCR or max pain gives an option *buyer* an edge after costs.

### Cited Findings
- **Pan & Poteshman (RFS 2006; NBER w10925)**: PCR defined as put buy-to-open volume / (put + call buy-to-open volume), built from a proprietary CBOE dataset. Stocks with low PCR beat stocks with high PCR by **>40 bp the next day and >1% over the next week** on a risk-adjusted basis. The predictability dies out over several weeks **with no reversal**, which the authors read as private information, not mispricing. Predictability is **stronger for stocks with more informed traders and for higher-leverage (OTM) contracts** — [CXO summary](https://www.cxoadvisory.com/sentiment-indicators/the-predictive-power-of-the-put-call-ratio-for-individual-stocks); [NBER](https://www.nber.org/papers/w10925)
- Sample is CBOE, 1990–2001, daily cross-sectional, individual stock options only. The quoted strategy is "40 bp per day and 1% per week" — [OptionMetrics research summary](https://optionmetrics.com/research/j-pan-and-a-m-poteshman-the-information-in-option-volume-for-stock-prices/)
- Key caveat (from the paper as summarised): the signal requires **buyer-initiated, open-position** volume. Plain public volume PCR, which mixes opening and closing trades and buyer- and seller-initiated trades, is a much noisier proxy — [CXO](https://www.cxoadvisory.com/sentiment-indicators/the-predictive-power-of-the-put-call-ratio-for-individual-stocks)
- **Ni, Pearson & Poteshman (JFE 2005, 78(1)) "Stock price clustering on option expiration dates"**: on expiry dates, optionable stocks close clustered at strike prices. Returns are altered by **at least 16.5 bp on average per expiry** (about $9bn market cap shifted). Drivers are market-maker delta-hedge rebalancing and manipulation by proprietary traders — [SUFE newsletter abstract](https://academicnewsletter.sufe.edu.cn/info/357561); [HKBU record](https://scholars.hkbu.edu.hk/en/publications/stock-price-clustering-on-option-expiration-dates-3/)
- Clustering is stronger at high-OI strikes and where the gamma-weighted position of hedgers is large (search summary of the same literature) — [OptionMetrics](https://optionmetrics.com/?p=1084)
- **Ni, Pearson, Poteshman & White (RFS 2021, 34(4)) "Does option trading have a pervasive impact on underlying stock prices?"**: hedge rebalancing by traders who hedge **raises** stock volatility when they are net *written* (short options) and **lowers** it when they are net *purchased* (long options). The paper finds a statistically and economically significant **negative relation between stock return volatility and the net purchased option positions of likely hedgers**, and an effect on the probability of large moves — [HKBU record](https://scholars.hkbu.edu.hk/en/publications/does-option-trading-have-a-pervasive-impact-on-underlying-stock-p/); [RePEc](https://ideas.repec.org/a/oup/rfinst/v34y2021i4p1952-1986..html)
- **Max pain — Filippou, Garcia-Ares & Zapatero (2022) "No Max Pain, No Max Gain: Stock Price Predictability at Options Expiration"**: decile portfolios formed on the 2nd Friday of each month and held one week (the expiry week). Long high-max-pain / short low-max-pain gives "very positive and statistically significant" returns and alphas. The effect is concentrated in **small, illiquid growth stocks**, and is linked to institutional rather than retail order imbalance — [Harbourfront Quant summary](https://harbourfrontquant.substack.com/p/max-pain-theory-in-practice)
- A search summary reports an average weekly long-short return of **about 0.4%** for this max-pain strategy. This number was not verified against the paper — [search summary via Harbourfront / OptionCharts](https://optioncharts.io/blog/how-accurate-is-max-pain-in-option-trading)
- **India, PCR, frequency-domain causality — Jena, Tiwari & Mitra (Economies 7(1), Mar 2019)**: on Nifty, **volume PCR predicts returns at about 2.5-day cycles and OI PCR at about 12-day cycles**. The authors advise matching the PCR type to the trading horizon. The abstract does not state the sign (contrarian vs confirming) — [RePEc/Economies](https://ideas.repec.org/a/gam/jecomi/v7y2019i1p24-d217055.html)
- **India, BankNifty — Singh & Chaudhary (ICFMCF 2023, IIT Delhi)**: regressed BankNifty returns on option volume and OI using one year of data. Explanatory power was "~50%", and the authors conclude predictability is **"significantly lesser than as propagated in the literature"** and **weaker for index options than for single-stock options** — [Harbourfront Quant summary](https://harbourfrontquant.substack.com/p/is-the-put-call-ratio-a-reliable)
- **India, highest-OI strike**: a study of Nifty near-month options (Jan 2014–Dec 2020) reports a **99.2% correlation** between the highest-OI strike and the next day's Nifty close — [Indian Journal of Finance via i-scholar](https://i-scholar.in/index.php/ijf/article/view/202397); [IJF article](https://www.indianjournalofentrepreneurship.com/index.php/IJF/article/view/72105)
- Indian sources describe OI-PCR as preferred over volume-PCR because it reflects committed positions, and treat PCR extremes as contrarian. These are practitioner and glossary descriptions, not tests — [Research360](https://www.research360.in/blogs/put-call-ratio-retail-vs-institutional-sentiment); [equitiesindia glossary](https://equitiesindia.com/glossary/nifty-put-call-ratio-extremes)

### Inferences
- The 99.2% "highest-OI strike vs next close" correlation is almost certainly a **levels correlation**. Both series track the index level, so the correlation would be near 1 even with zero predictive content. It says nothing about direction or returns. Treat it as non-evidence unless it is re-tested on changes (next-day return vs. distance to the OI-max strike).
- Pan-Poteshman's edge comes from a signal (signed open-buy volume) that **is not available on NSE**. NSE does not publish buy-to-open vs sell-to-open by strike. The closest public proxies are (a) daily participant-wise OI (Client/Pro/FII/DII long/short in index calls and puts) and (b) OI change combined with option price change at each strike (see Q3).
- The US evidence says informed option flow is in **single stocks and leveraged OTM contracts**, and decays over **days to weeks**. For a 1–4 session holding period on F&O stocks, a stock-level signed-flow or OI-change signal is a more promising research target than index PCR.
- Max pain and pinning are **expiry-day and expiry-week** effects of a few tens of bp. For a long-option buyer, that is smaller than one day of theta on a near-expiry contract. The practical use is negative: avoid buying expiry-day options when spot sits near a heavy OI or max-pain strike late in the session. Do not try to trade toward max pain.
- PCR at 2.5-day (volume) and 12-day (OI) cycles fits the user's 1–4 session holding period only for volume PCR. OI-PCR is more of a slow regime or sentiment filter.

### Gaps
- Exact t-stats and coefficients for Pan-Poteshman (beyond the 40 bp / 1% headline), Jena et al. (sign, sample period) and Filippou et al. (weekly return, t-stat) were not extractable this session.
- No found study tests NSE Nifty/BankNifty **max pain** convergence rigorously after the 2024–25 SEBI changes (one weekly expiry per exchange, larger lots). The "max pain accuracy" pages found are vendor blogs.
- No found peer-reviewed study of NSE **stock-option** expiry pinning (an Indian replication of Ni-Pearson-Poteshman).

---

## Q2. Evidence on dealer gamma (Baltussen et al., Barbon & Buraschi, SqueezeMetrics GEX, SpotGamma, 0DTE): does negative gamma bring trending and higher realized vol, and positive gamma bring mean reversion and pinning? Any evidence for NSE indices?

### Takeaway
The direction of the effect is well supported in US and global data. **Short gamma among hedgers goes with intraday momentum and higher volatility; long gamma goes with reversal, damped volatility and pinning.** The effects are mainly **intraday (last 30 minutes)**, they **revert the next day or at the next open**, and they are strongest in illiquid underlyings. GEX's apparent power over next-day volatility largely disappears once implied volatility is controlled for. A 0DTE study finds high 0DTE gamma is linked to **lower**, not higher, intraday volatility. **No credible NSE-specific GEX study was found.**

### Cited Findings
- **Baltussen, Da, Lammers & Martens, "Hedging Demand and Market Intraday Momentum" (JFE, Oct 2021)**: in 60+ futures on equities, bonds, commodities and FX from 1974 to 2020, the **last-30-minute return is positively predicted by the rest-of-day return**. The effect is economically and statistically highly significant and **reverts over the next days**. The paper links it to gamma-hedging demand from option market makers and leveraged ETFs: hedging short gamma means trading in the direction of the move — [Erasmus repository](https://pure.eur.nl/en/publications/hedging-demand-and-market-intraday-momentum/); [Tinbergen](https://tinbergen.nl/publication/168044/hedging-demand-and-market-intraday-momentum)
- A summary of the same paper says "market dynamics are strongly predictable intraday" and that concentrated trades by portfolio insurers, option market makers and leveraged ETFs have "substantial, non-fundamental price impact" — [Harbourfront Quant](https://harbourfrontquant.substack.com/p/market-intraday-momentum-and-hedging)
- Gao, Han, Li & Zhou (earlier related work): the **first half-hour return predicts the last half-hour return** of the US market — [Alpha Architect](https://alphaarchitect.com/2014/08/attention-prop-traders-the-first-half-hour-of-trading-predicts-the-last-half-hour/)
- **Barbon & Buraschi, "Gamma Fragility" (SSRN 3725454, rev. Mar 2021)**: using a stock-level gamma-imbalance proxy from a large equity-option panel, **intraday momentum (reversal) is explained by negative (positive) ex-ante gamma imbalance interacting with illiquidity**. The effect is stronger for the least liquid underlyings and helps explain intraday volatility and flash crashes — [abarbon.com](https://abarbon.com/papers/gamma-fragility); [St. Gallen repository](https://www.alexandria.unisg.ch/261487)
- **Barbon, Beckmeyer, Buraschi & Moerke (2021), "The Role of Leveraged ETFs and Option Market Imbalances on End-of-Day Price Dynamics"**: a large negative (positive) aggregate gamma imbalance, scaled by average dollar volume, gives significant **end-of-day momentum (reversal)**. Leveraged-ETF rebalancing effects are **economically larger** than gamma effects, and the effects **revert quickly at the next day's open** — [Harbourfront Quant](https://harbourfrontquant.substack.com/p/how-options-imbalances-affect-price)
- **Ni, Pearson, Poteshman & White (RFS 2021)**: net *written* options among hedgers raise underlying volatility, net *purchased* options lower it (stock-level, US) — [HKBU](https://scholars.hkbu.edu.hk/en/publications/does-option-trading-have-a-pervasive-impact-on-underlying-stock-p/)
- **SqueezeMetrics GEX (first published Mar 2016, revised Dec 2017)**: uses the simplifying heuristic that **"dealers are long calls, short puts"**. Per contract: GEX = sign × Γ × OI × multiplier × S² × 0.01, with sign +1 for calls and −1 for puts. This gives the dollar amount dealers trade to rehedge for a 1% move — [FlashAlpha API comparison (secondary)](https://flashalpha.com/articles/best-gex-api-2026-flashalpha-vs-spotgamma-vs-unusual-whales); [GEX white paper PDF](https://slopeofhope.com/wp-content/uploads/2020/11/GEX-white_paper.pdf)
- **Vendor backtest (FlashAlpha, treat with caution)**, SPY end-of-day, Apr 2018–Apr 2026, n = 1,972 days:
  - Raw GEX vs next-day realized vol: Spearman ρ = −0.36. Most-positive GEX quintile had 6.34% next-day RV vs 16.97% for the most negative (spread −10.6 vol points, t = −13.0).
  - After controlling for VIX, ρ = −0.14. After controlling for **VIX + ATM IV, ρ = −0.03 (p = 0.18), not significant**.
  - Inside high-VIX days (n = 493) the effect is absent (ρ = −0.02).
  - DEX vs next-day return: ρ = −0.03, not significant.
  - Vanna (VEX) and charm (CHEX) signals collapse after controls. VEX correlates +0.72 with VIX.
  - Source: [FlashAlpha](https://flashalpha.com/articles/gex-dex-vex-chex-8-year-backtest-spy-vix-control)
- **0DTE — Dim, Eraker & Vilkov, "0DTEs: Trading, Gamma Risk and Volatility Propagation" (2024)**: **no evidence that higher 0DTE OI gamma increases index volatility**. It is linked instead to **lower** intraday volatility. There is no propagation to overnight or lagged intraday volatility, and the 0DTE volatility risk premium is high — [Quantpedia](https://quantpedia.com/do-sp500-0dtes-options-increase-market-volatility/); [Harbourfront Quant](https://harbourfrontquant.substack.com/p/the-rise-of-0dte-options-cause-for)
- GEX critique (vendor/educational): OI-based GEX assigns dealer sides **by convention**, because public OI does not identify dealer vs customer. It ignores whether flows open or close positions and how continuously dealers rehedge. One source cites a "30–50% error margin". That figure is unsourced and should be treated as anecdotal — [LuxAlgo](https://www.luxalgo.com/library/concept/gamma-exposure/); [SpotGamma](https://spotgamma.com/gamma-exposure-gex/)
- **India context**: SEBI's FY26 study (released 20 Aug 2026, PR 50/2026):
  - About 97% of individual F&O traders are predominantly **option buyers** and about 2% are sellers.
  - 87.7% of individuals lost money (aggregate net loss Rs 91,685 cr).
  - Proprietary traders earned about Rs 44,000 cr gross and FPIs about Rs 14,000 cr, with 99% of their profits made by algo entities.
  - **59% of index-option turnover was same-day-expiry (0DTE)** and 97% was within one week of expiry.
  - Source: [CorpLawUpdates summary of SEBI study](https://www.corplawupdates.in/updates/sebi-equity-derivatives-retail-trader-study-fy26); [PolicyEdge](https://www.policyedge.in/p/nearly-9-in-10-individual-fo-traders-lost-money-as-participation-fell-18-sebi-studies)
- FY25: 91% of individuals lost money, net loss Rs 1,05,603 cr. Losses were concentrated in weekly index options — [Moneylife](https://moneylife.in/article/91-percentage-of-retail-traders-lost-money-in-derivatives-losses-in-fo-surged-41-percentage-to-rs105-lakh-crore-in-fy2425-sebi-study/77613.html); [Business Standard](https://www.business-standard.com/amp/markets/capital-market-news/sebi-sees-dip-in-derivatives-turnover-91-of-retail-traders-lose-money-in-fy25-125070800660_1.html)
- Indian commentary claims Nifty weekly expiry gamma "amplifies short-term momentum" as writers hedge. This is glossary-level assertion, not a test — [equitiesindia glossary](https://equitiesindia.com/glossary/nifty-weekly-options-dynamics)

### Inferences
- **The sign convention is likely wrong for India.** The SqueezeMetrics convention ("dealers long calls, short puts") encodes the US pattern where customers buy puts for protection and overwrite calls. In NSE index options, SEBI data show individuals are overwhelmingly **net buyers of both calls and puts**, and the counterparty is prop/algo and FPI sellers. A more defensible Indian prior is **"liquidity providers short both calls and puts near the money."** That makes aggregate hedger gamma negative near spot most of the time, especially on expiry day. Under this alternative convention GEX = −Σ Γ × OI × lot × S² × 0.01 over both calls and puts, so the "gamma flip" concept largely disappears. The better Indian metric is the **size and concentration of near-ATM short gamma (|GEX| per Rs crore of underlying liquidity)**, not its sign. Validate with NSE participant-wise OI (Client vs Pro vs FII long/short calls and puts) before trusting either sign.
- The Dim-Eraker-Vilkov 0DTE result and the Indian prop/algo seller base point the same way. Sophisticated, well-capitalised sellers may hedge discretely or not at all, and may hold offsetting positions. So "short gamma ⇒ trend" is not guaranteed intraday on Nifty expiry days. This needs testing on NSE data.
- For an option buyer, the most usable form of the gamma literature is **"trade in the direction of the day's move into the last hour, then exit by the close or next open."** The momentum is intraday and reverts at the next open or over the next few days, which argues against carrying gamma-motivated positions overnight. This conflicts with 2–4 session holds unless MACD supplies the multi-day reason.
- GEX's information about **realized volatility** is largely already in IV (per the FlashAlpha controls). For an option buyer, the relevant quantity is expected RV relative to IV. A GEX feature adds value only if it predicts RV − IV, not RV alone. Test it that way.

### Gaps
- **No peer-reviewed or NSE/SEBI study found that computes GEX or dealer gamma for Nifty/BankNifty** and tests it against intraday momentum or realized vol.
- Whether Baltussen et al. include Indian index futures in the "60+ futures" sample was not confirmed. Their exact gamma sign construction, coefficients and t-stats were not extracted (the PDF was not machine-readable here).
- No independent (non-vendor) evidence found for **vanna/charm flow** predictability (e.g., "charm-driven drift into OPEX"). Treat those as practitioner narratives.
- SpotGamma's "Call Wall / Put Wall / Hedge Wall" methodology is proprietary. No out-of-sample tests were found.

---

## Q3. How should OI change be combined with price change, and what does evidence say about stock-option OI vs index-option OI?

### Takeaway
The standard 2×2 "buildup" grid is a **descriptive convention from Indian brokers, not an empirically validated signal**. No peer-reviewed test of its forward returns was found. The only rigorous signed-flow evidence (Pan-Poteshman) says informed option flow lives in **single-stock** options and leveraged OTM contracts. Indian work likewise finds index-option volume/OI less predictive than single-stock options.

### Cited Findings
- Definitions used on NSE:
  - **Long buildup** = OI up and price up.
  - **Short buildup** = OI up and price down (new shorts entering).
  - **Short covering** = OI down and price up.
  - **Long unwinding** = OI down and price down.
  - Sources: [onetradejournal](https://onetradejournal.com/glossary/short-buildup); [equitiesindia glossary](https://equitiesindia.com/glossary/open-interest-buildup-analysis); daily broker derivative snapshots such as [iBulls](https://www.ibullssecurities.com/public/companyreports/DerivativeSnapshotJuly10_1568624337.pdf)
- Searches for empirical (academic) tests of the buildup classification on NSE futures or options returned only descriptive broker material — [search result set incl. iBulls/onetradejournal](https://onetradejournal.com/glossary/short-buildup)
- Pan-Poteshman: predictability is greater for stocks with more informed traders and for higher-leverage contracts. The evidence is on individual stocks — [CXO](https://www.cxoadvisory.com/sentiment-indicators/the-predictive-power-of-the-put-call-ratio-for-individual-stocks)
- Singh & Chaudhary: predictability is **weaker for index options than for single-name stock options** (BankNifty, one-year sample) — [Harbourfront Quant](https://harbourfrontquant.substack.com/p/is-the-put-call-ratio-a-reliable)
- NSE publishes intraday OI-change analysis for major futures in **15-minute slots** from 9:15 to 15:30. A TradingView script description gives this as a common analysis granularity — [TradingView script](https://it.tradingview.com/script/Hyy4IjIG-NSE-Multi-Script-Open-Interest-Change-Analysis-by-JITENDRA)
- NSE **participant-wise OI** (Client, DII, FII, Pro; long and short contract counts in index futures, stock futures, index calls/puts, stock calls/puts) is published **daily after the close** — [AlgoTest explainer](https://algotest.in/blog/participant-wise-open-interest/); [Quantsapp participant data](https://web.quantsapp.com/participant-data)

### Inferences
- **Apply the grid to the option contract, not only to the future.** For a specific strike, "OI up + option premium up" means new buyers are paying up (aggressive opening buys), and "OI up + premium down" means new writers. This is a crude proxy for Pan-Poteshman's buy-to-open volume. Before using it, separate the premium change caused by the underlying from the premium change caused by IV. Practical proxy: compare the option's price change with delta × ΔS. If the premium rises more than delta explains, with rising OI, that suggests demand-driven IV bid from opening buyers.
- A plausible, **untested** feature set for a 1–4 session F&O stock trade:
  1. Futures buildup class on the trade timeframe (e.g., 15-min or daily), agreeing with the MACD direction.
  2. ΔOI as % of prior OI at the 2–3 strikes nearest ATM on the trade side vs the opposite side (a call-side vs put-side net opening flow ratio).
  3. A stock-level volume PCR change vs its own 20-day median, rather than an absolute level.
  The literature supports stock-level flow over index flow, and multi-day over intraday decay. These features should be validated on the user's own backtest before use.
- For **index options**, retail is about 97% buyers and 0DTE dominates turnover. So OI changes on Nifty/BankNifty mostly reflect writer (prop/algo) positioning, not "smart money" buying. Huge call-side OI additions mostly mean writers are confident of a cap, and the resulting levels act as soft intraday resistance. This is consistent with the pinning and long-gamma literature only if writers hedge, which is uncertain.
- Daily participant-wise OI is the only public NSE data that **identifies who** is long or short index calls and puts. A daily feature such as "FII net index-option position change" or "Client net long index calls minus puts" is computable for next-day bias. No validated evidence of its predictive value was found.

### Gaps
- No peer-reviewed study found that tests forward returns of NSE OI buildup classes (futures or options) at intraday or 1–5 day horizons.
- No study found on the predictive value of NSE participant-wise OI (FII/Pro/Client) for next-day Nifty returns. Practitioner sites show charts only.

---

## Q4. Practical pitfalls (OI refresh lag on NSE/Fyers, sign assumptions, retail-heavy market) and how to compute the features from a chain snapshot

### Takeaway
On NSE, option **OI is refreshed by the exchange roughly every 3 minutes** for most strikes, not tick-by-tick. Fyers' option-chain API is a REST snapshot (OI in the websocket was a pending feature as of the community posts found). Final OI changes after 15:30 because of post-close clearing settlement. Any intraday OI feature must therefore be sampled at 3–15 minute resolution and should never be compared with the previous day's end-of-day OI from a different snapshot time. Retail-net-buyer dominance means US dealer-sign conventions should not be imported blindly.

### Cited Findings
- Fyers community (7 Nov 2024): **"The OI data is officially updated every 3 min by NSE for majority of strikes."** — [Fyers community](https://fyers.in/community/t/option-chain-update-frequqency/21050)
- The Fyers option-chain API is a snapshot of data at request time. For continuous data, users are pointed to websockets. OI in the websocket was requested and described by staff as "coming soon". Option OI is available from the option-chain call and futures OI from market depth, **one symbol per request** — [Fyers community: OI data on websockets](https://fyers.in/community/questions-5gz5j8db/post/oi-data-on-websockets-EODmKqi0gp5udth); [Fyers Options OI & IV](https://fyers.in/community/t/options-oi-iv/12918)
- Zerodha Kite forum: OI at 15:30 differs from OI after about 16:30 because of **clearing-member trade settlements** after the close (volume is unchanged, OI changes). Use the exchange timestamp in websocket ticks to know when OI was recorded — [Kite forum](https://kite.trade/forum/discussion/comment/19194/)
- Participant-wise OI is end-of-day only — [AlgoTest](https://algotest.in/blog/participant-wise-open-interest/)
- Max-pain "accuracy" claims depend on when the value was recorded, which expiries were included, whether intraday OI changes were used, and the tolerance for "close enough" — [search summary of OptionCharts/TrueData](https://optioncharts.io/blog/how-accurate-is-max-pain-in-option-trading); [TrueData](https://www.truedata.in/blog/max-pain-theory-does-it-really-work)
- GEX sign is a convention, not an observation — [LuxAlgo](https://www.luxalgo.com/library/concept/gamma-exposure/)
- About 97% of individual traders are option buyers, and 59% of index-option turnover is 0DTE (FY26) — [CorpLawUpdates/SEBI](https://www.corplawupdates.in/updates/sebi-equity-derivatives-retail-trader-study-fy26)
- GEX formula per contract (US convention): sign × Γ × OI × multiplier × S² × 0.01 — [FlashAlpha (secondary)](https://flashalpha.com/articles/best-gex-api-2026-flashalpha-vs-spotgamma-vs-unusual-whales)

### Inferences (computation recipes; standard definitions, adapted to NSE. Validate before use)
- **Inputs from a chain snapshot (Fyers):** per strike K and option type: OI, previous-day OI (or OI change), volume, LTP, IV (compute it if absent), spot or future S, time to expiry T in years (use trading minutes to the 15:30 expiry close), risk-free rate r (about 6–7%; a small effect intraday), lot size L. Use the **future price** (or put-call-parity forward) as S for index options to avoid dividend and carry bias.
- **PCR:**
  - OI-PCR = Σ put OI / Σ call OI.
  - Volume-PCR = Σ put volume / Σ call volume.
  - Prefer **near-ATM bands** (e.g., ±5 strikes or ±3% of S) and the **nearest-expiry plus next-expiry** split. Total-chain PCR is dominated by far-OTM lottery strikes.
  - Use a z-score vs a rolling 20-day median taken at the same time of day. PCR has strong intraday and expiry-cycle seasonality.
  - Jena et al. suggest volume-PCR for horizons of about 2–3 days and OI-PCR for about 2 weeks.
- **OI walls / support-resistance:** call wall = argmax_K call OI above S; put wall = argmax_K put OI below S. Also track **ΔOI walls** (largest intraday OI additions), which react faster than stock OI. Weight by gamma (Γ·OI) rather than raw OI so that far-OTM strikes do not dominate. Treat walls as soft levels on expiry day and in the expiry week only. Evidence is limited to pinning studies (Ni-Pearson-Poteshman), not to non-expiry support or resistance.
- **Max pain:** for each candidate settlement price X in the strike grid, Pain(X) = Σ_K [callOI(K)·max(X−K,0) + putOI(K)·max(K−X,0)]·L, and MaxPain = argmin_X Pain(X). Use only the expiring series. Its meaningful window is the last 1–2 sessions before expiry. Recompute it from a timestamped snapshot.
- **GEX (dollar gamma per 1% move), with an explicit sign choice:**
  - Black-Scholes Γ(K) = φ(d1)/(S·σ·√T).
  - GEX_K = s_K × Γ(K) × OI_K × L × S² × 0.01, where s_K is the assumed sign of hedger holdings.
  - US convention: s = +1 for calls and −1 for puts.
  - India alternative ("liquidity providers short what retail buys"): s = −1 for both calls and puts in the nearest expiry.
  - Better: estimate s from participant-wise OI. If Pro+FII are net short index calls and net short index puts, use −1 for both, and scale by their share of total short OI.
  - Report total GEX, GEX per Rs crore of average daily futures turnover (the Barbon-Buraschi illiquidity interaction), and a **gamma profile** by recomputing GEX on a grid of hypothetical S values ±3%. The "flip" is the S where the profile crosses zero. It is only meaningful under the US-style mixed-sign convention.
- **Vanna / charm:** vanna = ∂Δ/∂σ = −φ(d1)·d2/σ. Charm = −∂Δ/∂T. Aggregate them the same way, signed × OI × L. No independent evidence supports them as predictors, so treat them as optional research features.
- **Known flaws of retail GEX estimates:**
  1. Dealer side is assumed, not observed.
  2. OI is end-of-period stock, ignoring intraday opening/closing churn. On NSE 0DTE, most gamma is created and destroyed within the day, while OI updates only every ~3 min and settles after the close.
  3. Black-Scholes gamma with a single IV ignores skew and the sticky-strike vs sticky-delta assumption.
  4. Hedging is assumed continuous and complete. Prop/algo books net across strikes, expiries, futures and stock, so gross OI greatly overstates net hedging need.
  5. Expiry-day gamma explodes as T→0, so GEX is dominated by a few ATM strikes and becomes numerically unstable in the last hour. Cap T at a minimum, e.g., 15–30 minutes.
  6. GEX's volatility information overlaps heavily with IV and VIX (FlashAlpha controls), so a backtest must include India VIX and ATM IV as controls.
- **Timing hygiene for backtests:**
  - Store chain snapshots with exchange timestamps.
  - Compute features only from snapshots strictly before the signal bar's close.
  - Remember OI moves in ~3-minute steps.
  - Avoid using 15:30 OI for same-day decisions, because post-close settlement revises it.
  - Participant-wise OI is only usable from the **next** session.

### Gaps
- No official NSE circular was found stating the intraday OI dissemination interval. The "3 minutes" figure comes from a Fyers community moderator, and NSE may differ by segment or strike. This needs confirmation from NSE's market-data documentation or by measuring tick timestamps empirically.
- The current status (Oct 2026) of OI in the Fyers websocket feed was not confirmed. The available posts date to 2024.
- No study was found that quantifies how much of NSE index-option OI is hedged by delta-hedgers vs held as naked or spread writes. This fraction is the main unknown in any Indian GEX estimate.
