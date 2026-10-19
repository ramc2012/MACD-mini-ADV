# Classical ML for Cross-Sectional Stock Picking (tree ensembles, regularized linear, shallow NNs)

Research date: 3 Oct 2026. Study dates are flagged inline. "US" = CRSP/S&P 500 universes unless noted; EM = emerging markets.

## 1. What did the landmark studies find, and what survives transaction costs and excluding microcaps?

### Takeaway
The headline academic results are large: GKX 2020 (US, monthly) gets a value-weighted long-short Sharpe of 1.35, and Leippold et al. 2022 (China) gets about 3.45. Three things shrink them sharply: excluding microcaps and hard-to-arbitrage stocks (Avramov et al.: about 62% of the payoff goes away without microcaps), realistic trading costs (high-frequency stat-arb Sharpe falls from about 4.7 to 1.8, and was about zero after 2010), and recency (large-cap gains in the US were roughly zero over 2001–2020). The predictability that survives is in less-efficient, retail-heavy markets (China) and in longer-horizon, lower-turnover setups.

### Cited Findings
**Gu, Kelly & Xiu (GKX), "Empirical Asset Pricing via Machine Learning" (NBER WP Dec 2018; RFS 2020), US**
- Data: about 30,000 CRSP stocks from Mar 1957 to Dec 2016 (more than 6,200 per month on average). Inputs are 94 firm characteristics, their interactions with 8 macro variables, and 74 industry dummies, for more than 900 baseline signals. — [GKX NBER WP](https://www.nber.org/papers/w25398.pdf)
- Setup: 18 years of training (1957–74), 12 years of validation (1975–86) and 30 years out-of-sample (1987–2016). Models are refit once a year on an expanding window, with no cross-validation so time ordering is kept. Characteristics are lagged (monthly by at least 1 month, quarterly by at least 4 months, annual by at least 6 months) to avoid look-ahead. — [GKX NBER WP](https://www.nber.org/papers/w25398.pdf)
- Monthly stock-level out-of-sample R² (%), all stocks: OLS (all variables) −3.46; OLS-3 (size, B/M, momentum) 0.16; PLS 0.27; PCR 0.26; ENet 0.11; GLM 0.19; RF 0.33; GBRT 0.34; NN1 0.33; NN2 0.39; **NN3 0.40**; NN4 0.39; NN5 0.36. — [GKX NBER WP, Table 1](https://www.nber.org/papers/w25398.pdf)
- Top-1,000 (large) stocks: RF 0.63, GBRT 0.52, NN3 0.70, ENet 0.25, OLS −11.28. Bottom-1,000: NN3 0.45, RF 0.35, PLS 0.42. The authors argue this shows the gains are "not merely ... small scale inefficiencies driven by illiquidity". — [GKX NBER WP, Table 1](https://www.nber.org/papers/w25398.pdf)
- Annual-horizon R² is about 10x larger: all stocks NN4 3.60, NN3 3.40, RF 3.28, GBRT 3.09, PCR 3.08, OLS-3 2.50, ENet 1.78. Large stocks: NN4 4.91, RF 4.80, GBRT 4.07. — [GKX NBER WP, Table 2](https://www.nber.org/papers/w25398.pdf)
- Portfolios: the best decile long-short (10-1) is NN4, value-weighted, at 2.3% per month (27.1% a year) with 20.1% annual volatility, for a Sharpe of **1.35**. Equal-weighted, the Sharpe is **2.45**. Excluding stocks below the NYSE 20th size percentile, the equal-weighted NN4 Sharpe drops to **1.69**. — [GKX NBER WP](https://www.nber.org/papers/w25398.pdf)
- An equal-weight ensemble of all 11 ML models reaches R² of 0.43% per month, an equal-weighted Sharpe of 2.49 and a value-weighted Sharpe of 1.33. — [GKX NBER WP](https://www.nber.org/papers/w25398.pdf)
- Turnover: NN1–NN5 portfolios turn over 110–130% per month, and tree and penalized-linear portfolios slightly more. For reference, a short-term-reversal decile spread turns over 172.6% per month and a size spread 22.9%. The paper reports gross returns only. — [GKX NBER WP](https://www.nber.org/papers/w25398.pdf)
- Market timing: aggregating stock-level NN forecasts to the S&P 500 gives a monthly R² of 1.08–1.80% for trees and NNs, and a timing Sharpe of 0.77 against 0.51 for buy-and-hold. — [GKX NBER WP](https://www.nber.org/papers/w25398.pdf)

**Krauss, Do & Huck (2017, EJOR 259(2)), S&P 500 daily statistical arbitrage**
- Setup: survivor-bias-free S&P 500 constituents from Dec 1992 to Oct 2015. Inputs are 31 lagged-return features (one trading year, about 240 days). The target is binary: whether the stock's next-day return beats the cross-sectional median. Each day the top 10 are bought and the bottom 10 shorted. Models are trained on 750 days and trade the next 250. — [Krauss et al. 2016 WP](https://www.econstor.eu/bitstream/10419/130166/1/856307327.pdf)
- Gross daily returns at k=10: ensemble (ENS) 0.45%, random forest (RAF) 0.43%, GBT 0.37%, DNN 0.33%. After costs (the paper assumes 5 bps per half-turn), ENS falls to 0.25% per day. — [Krauss et al. WP](https://www.econstor.eu/bitstream/10419/130166/1/856307327.pdf)
- Annualized Sharpe before and after costs: DNN 2.44→0.55, GBT 3.94→1.23, RAF 5.12→1.90, ENS 4.71→1.81 (market 0.35). — [Krauss et al. WP, Table 2](https://www.econstor.eu/bitstream/10419/130166/1/856307327.pdf)
- Decay by sub-period:
  - 12/92–03/01: after-cost returns above 200% a year, Sharpe 6.7.
  - 04/01–08/08: 22% a year, Sharpe 0.51. RF did best here (about 35% a year, Sharpe close to 1).
  - Global financial crisis: more than 400% a year, Sharpe 4.5.
  - Final sub-period (to Oct 2015): "deterioration".
  - The authors attribute the decay to the spread of RF (2001) and gradient boosting (2002) in industry. — [Krauss et al. WP](https://www.econstor.eu/bitstream/10419/130166/1/856307327.pdf)
- Fischer & Krauss (2018, EJOR 270(2)) ran an LSTM on the same S&P 500 setup. Outperformance is clear for 1992–2009, but from 2010 "excess returns seem to have been arbitraged away", with profitability around zero after costs. — [Fischer & Krauss 2018](https://ideas.repec.org/a/eee/ejores/v270y2018i2p654-669.html)

**Avramov, Cheng & Metzker, "Machine Learning vs. Economic Restrictions" (Management Science 69(5), 2023), US**
- Deep-learning signals earn their profits in hard-to-arbitrage stocks and in high limits-to-arbitrage periods. Payoffs fall **62% excluding microcaps**, **68% excluding non-rated firms**, and **80% excluding distressed firms** around credit-rating downgrades. — [SSRN](https://papers.ssrn.com/abstract=3450322); [CUHK summary](https://cbk.bschool.cuhk.edu.hk/the-limitations-of-using-ai-to-pick-stocks/)
- Performance "deteriorates in the presence of reasonable trading costs due to high turnover and extreme positions". The signals are still profitable in long positions, in recent years, and with low downside risk. — [INFORMS](https://pubsonline.informs.org/doi/fpi/10.1287/mnsc.2022.4449); [SSRN](https://papers.ssrn.com/abstract=3450322)

**Leippold, Wang & Zhou, "Machine Learning in the Chinese Stock Market" (JFE 145(2), 2022), China A-shares**
- Liquidity is the most important predictor, unlike in the US. Retail-investor dominance drives short-horizon predictability, especially in small stocks. Large stocks and SOEs are unusually predictable at longer horizons. Results remain significant after costs. — [SFI](https://www.sfi.ch/en/publications/machine-learning-in-the-chinese-stock-market); [SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3754339)
- Long-short Sharpe peaks at **3.45 (NN3)**, against 1.35 in GKX for the US. For benchmark NN4, the long-short Sharpe goes from **2.91 to 2.76 at 20 bps round-trip** and to **2.34 at 80 bps**. Long-only goes from 1.68 to 1.46 at 80 bps. — [ScienceDirect / JFE](https://www.sciencedirect.com/science/article/pii/S0304405X21003743) (numbers from the search-engine summary of the paper text; I could not open the full text to verify).

**Later robustness and decay studies**
- Cakici, Fieberg, Metko & Zaremba (2023): US, 153 characteristics, 1972–2020. ML predictability works for short horizons, small caps and early data, and fails in the opposite cases.
  - Monthly alphas are about 2x yearly alphas.
  - Monthly forecasts "extract information largely from trading frictions".
  - For yearly forecasts in large firms (90% of US market cap) over 2001–2020, abnormal returns were −3.66% to −0.36% a year, none significant.
  - Sources: [Quantpedia summary](https://quantpedia.com/decreasing-returns-of-machine-learning-strategies/); [SSRN 4552395](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4552395)
- Blitz, Hanauer, Hoogteijling & Howard, "The Term Structure of ML Alpha" (Robeco, Jul 2023; SSRN 4474637). One-month-horizon ML models show impressive gross alphas but were "mainly profitable before 2004". Net of costs they are close to zero after 2004. Models trained on 3-, 6- and 12-month targets do much better net over 2004–2021 because turnover is lower. — [Robeco](https://www.robeco.com/en-ch/insights/2023/07/the-term-structure-of-machine-learning-alpha)
- Azevedo, Hoegner & Velikov, "The Expected Returns on ML Strategies" (2023). Across 9 ML strategies, costs cut returns by 13–40%. The LSTM still nets up to 1.42% per month (6-factor alpha 1.20%, t = 3.46) despite turnover above 50%. Most cost-mitigation techniques reduced net returns; only a 2-month holding period helped slightly. — [Quantpedia](https://quantpedia.com/the-expected-returns-of-machine-learning-strategies/); [AFA](https://afajof.org/management/viewp.php?n=75544)

### Inferences
- Gross ML Sharpe ratios from monthly or daily US panels should be cut by about half or more before use. Discount for microcaps (about −60%), costs, and post-2004 or post-2010 decay. Large-cap, annual-horizon ML in the US has shown little reliable alpha over the last 20 years.
- The markets where ML holds up best after costs have heavy retail participation, low institutional arbitrage capital and liquidity frictions (China). India's cash market shares some of these traits, so it is closer to China than to the US. That points to a better prior than the US for a liquidity- and reversal-aware model, but this is an inference, not a tested result.

### Gaps
- I could not open the Leippold et al. full text to check the exact R² figures or the cost-adjusted tables. The Sharpe numbers above come from a search summary of the paper.
- GKX report gross portfolios only. I found no published after-cost Sharpe for GKX itself.
- I could not retrieve the exact Krauss et al. 2010–2015 sub-period figures. The text says "deterioration", and Fischer & Krauss say "around zero after costs".

## 2. Gradient boosting vs random forest vs linear/elastic net vs MLP: which wins, at what horizon?

### Takeaway
On monthly characteristic panels, shallow NNs (2–3 layers) and tree ensembles beat penalized linear models by about 2x in R², and depth beyond 3 layers does not help. GBRT/RF are roughly tied with NNs on large stocks, and ensembles of model types are the most robust. On daily price-only panels, RF/GBT match or beat DNNs (Krauss et al.). In Qlib's CSI300 daily benchmark, GBDTs on hand-made technical features (Alpha158) beat linear models and MLPs on IC. In recent test windows, gains over linear models are small, and in at least one report net excess return is negative. Horizon matters more than model choice for net returns: longer targets (3–12 months) survive costs and 1-month or daily targets mostly do not.

### Cited Findings
- **GKX 2020 (US, monthly).** Ranking: NN3 0.40 ≈ NN2/NN4 0.39 > GBRT 0.34 ≈ RF 0.33 ≈ NN1 > PLS/PCR ≈ 0.27 > GLM 0.19 > OLS-3 0.16 > ENet 0.11 >> OLS −3.46. "The benefits of 'deep' learning are limited": 4- and 5-layer nets do not beat NN3. The gains come from nonlinear interactions, since GLM (splines without interactions) fails to beat linear models. — [GKX NBER WP](https://www.nber.org/papers/w25398.pdf)
- **Krauss et al. 2017 (US large caps, daily, lagged returns only).** Gross daily return: RF 0.43% > GBT 0.37% > DNN 0.33%, and the equal ensemble 0.45% is best. RF has the lowest tail risk and the DNN the highest. — [Krauss et al. WP](https://www.econstor.eu/bitstream/10419/130166/1/856307327.pdf)
- **Hanauer & Kalsbach, "ML and the Cross-Section of EM Stock Returns" (Nov 2022; Robeco).** Sample: 32 emerging markets, 15,152 stocks, Jul 1995 to Dec 2021, out-of-sample from Jan 2002, 36 characteristics. Long-short returns are about 0.8% per month for OLS and ENet, about 1.0% for RF and GBRT, and about 1.2% for NNs. — [Alpha Architect / Robeco summaries via search](https://alphaarchitect.com/machine-learning-and-emerging-market-stock-returns/); [CXO](https://www.cxoadvisory.com/equity-premium/machines-picking-emerging-market-stocks); [Robeco](https://robeco.com/en-hk/insights/2023/10/using-machine-learning-for-emerging-market-equity-returns)
- **Qlib CSI300 benchmark, Alpha158 features (daily, 20 seeds).** Annualized excess return (ARR) is after costs; IR is the information ratio.

  | Model | IC | Rank IC | ARR | IR |
  |---|---|---|---|---|
  | Linear | 0.040 | 0.047 | 6.9% | 0.92 |
  | LightGBM | 0.045 | 0.047 | 9.0% | 1.02 (MaxDD −10.4%) |
  | XGBoost | 0.050 | 0.051 | 7.8% | 0.91 |
  | CatBoost | 0.048 | 0.045 | 7.7% | 0.80 |
  | MLP | 0.038 | 0.043 | 9.0% | 1.14 |
  | DoubleEnsemble (boosted GBDT ensemble) | 0.052 | n/r | 11.6% | 1.34 |
  | LSTM | 0.032 | n/r | 3.8% | 0.56 |
  | Transformer | 0.026 | n/r | 2.7% | 0.40 |

  — [Qlib benchmarks README](https://github.com/microsoft/qlib/blob/main/examples/benchmarks/README.md)
- **Qlib on Alpha360 (raw 60-day price/volume windows).** Sequence models do better here: GRU IC 0.049 / IR 0.97, TRA 0.049 / IR 1.28, against LightGBM IC 0.040 / ARR 5.6% / IR 0.76. With raw inputs, recurrent nets can learn the features themselves. With engineered features, GBDT is the stronger baseline. — [Qlib benchmarks README](https://github.com/microsoft/qlib/blob/main/examples/benchmarks/README.md)
- **A 2026 arXiv agent paper reran Qlib-style CSI300 Alpha158 baselines.** It reports LightGBM IC 0.063 but annualized return −0.1% (IR −0.007), Linear IC 0.025 / −5.1%, and CatBoost IC 0.050 / −2.1%. Its test window differs from the official README. This conflicts with the README numbers above and suggests baseline alphas are regime-dependent and decayed in later windows. — [arXiv 2608.28632](https://arxiv.org/pdf/2608.28632) (figures from search summary; I did not verify the test period)
- **Horizon.** Short-horizon (1-month) ML alpha is mostly eaten by costs after 2004. 3-, 6- and 12-month targets stay positive net. — [Robeco 2023](https://www.robeco.com/en-ch/insights/2023/07/the-term-structure-of-machine-learning-alpha). Monthly forecasts depend on frictions and small caps; annual forecasts depend on fundamentals and show weak large-cap alpha after 2000. — [Quantpedia on Cakici et al.](https://quantpedia.com/decreasing-returns-of-machine-learning-strategies/)

### Inferences
- In practice: on tabular feature panels, a LightGBM or XGBoost ranker or regressor with heavy regularization is the default first model, and it is hard to beat. A 2–3-layer MLP is a useful second model. An equal-weight ensemble of the two usually beats either one alone (GKX, Krauss, Qlib DoubleEnsemble). Elastic net is a robust sanity baseline, and its IC is often 70–90% of the GBDT's at much lower overfitting risk.
- For a MACD-style daily or weekly system on NSE, expect a rank IC around 0.03–0.05 from technical-only features (by analogy with Qlib CSI300). That is enough for ranking or filtering, but net profitability will depend on turnover control.

### Gaps
- I found no rigorous head-to-head of LightGBM vs CatBoost vs XGBoost on Indian equities.
- Weekly-horizon comparisons are sparse. Most studies are daily or monthly.

## 3. Most predictive feature families; how technical-indicator ML performs

### Takeaway
Across the US, China and EMs, the dominant predictors are price trend (short-term reversal, 12-month momentum, momentum change, industry momentum, maximum return), liquidity (turnover, dollar volume, Amihud illiquidity, spreads, size) and risk (total and idiosyncratic volatility, beta). In China, liquidity ranks first. Fundamentals matter more at annual horizons. Indicator-style features such as MACD and RSI are mostly transforms of the same price-trend and reversal information. In Qlib-style daily setups they produce modest but real IC. Retail-grade studies that claim very high MACD/RSI accuracy are low quality.

### Cited Findings
- GKX: all methods agree on the same dominant signals, "variations on momentum, liquidity, and volatility". There are four groups:
  - **Price trend**: 5 of the top 7 variables. These are mom1m (short-term reversal), mom12m, chmom, indmom, maxret and mom36m.
  - **Liquidity**: turn, std_turn, mvel1 (size), dolvol, ill (Amihud), zerotrade, baspread.
  - **Risk**: retvol, idiovol, beta, beta².
  - **Valuation and fundamentals**: lower importance at the monthly horizon, partly because they update infrequently.
  - At the annual horizon, price trend loses importance to liquidity and risk, and industry (sic2) gains. — [GKX NBER WP](https://www.nber.org/papers/w25398.pdf)
- Krauss et al.: lagged returns alone (31 features, 1–240 days) are enough for 0.45% per day gross on the S&P 500. They also cite earlier work where the most recent returns carry the "highest explanatory power", even when 86 firm characteristics are added. — [Krauss et al. WP](https://www.econstor.eu/bitstream/10419/130166/1/856307327.pdf)
- China: liquidity is the most important predictor. Retail-driven short-term predictability is strongest in small stocks. — [SFI / Leippold et al.](https://www.sfi.ch/en/publications/machine-learning-in-the-chinese-stock-market)
- Monthly ML forecasts "extract information largely from trading frictions", while yearly forecasts use fundamentals. — [Quantpedia on Cakici et al. 2023](https://quantpedia.com/decreasing-returns-of-machine-learning-strategies/)
- **Technical-only ML (Qlib Alpha158).** Alpha158 is 158 hand-made price and volume features: K-bar shape, ROC, MA, STD, beta/R²/residual of rolling regressions, max/min, quantiles, rank, RSV, correlation of price and volume, count of up days, volume MA and std, all over 5–60-day windows. With LightGBM it reaches IC of about 0.045, rank ICIR about 0.39 and IR about 1.0 net on CSI300 (2017 to Aug 2020). — [Qlib benchmarks](https://github.com/microsoft/qlib/blob/main/examples/benchmarks/README.md); [Qlib LightGBM Alpha158 config](https://raw.githubusercontent.com/microsoft/qlib/main/examples/benchmarks/LightGBM/workflow_config_lightgbm_Alpha158.yaml)
- **Daily XGBoost rankings on 196 technical predictors (MDPI *Economies* 14(9), 2026).** The study uses 1,094 walk-forward test iterations and compares four XGBoost objectives on the same features, asking whether daily ML rankings survive trading costs. — [MDPI](https://www.mdpi.com/2227-7099/14/9/393) (full text blocked with 403; results not verified)
- A 2024 arXiv study of random forests with technical indicators found raw price features beat technical indicators in importance. It also found overfitting and poor out-of-sample generalization. — [arXiv 2412.15448](https://arxiv.org/html/2412.15448v1)
- Low-quality claims such as "RSI 92% signal accuracy, MACD ~55%" on Indonesia's LQ45 come from weak venues and should not be relied on. — [ResearchGate](https://www.researchgate.net/publication/392317792_Analysis_of_the_Effectiveness_of_RSI_and_MACD_Indicators_in_Addressing_Stock_Price_Volatility)

### Inferences
- For an NSE MACD system, the highest-value additions to an ML layer are likely:
  - short-term reversal (1–5 day returns)
  - 3–12-month momentum and industry momentum
  - turnover and Amihud illiquidity
  - realized and idiosyncratic volatility
  - maximum daily return over the past month
  - delivery percentage and F&O open-interest data (India-specific, untested here)
- MACD itself is a smoothed momentum and reversal oscillator. Expect it to be largely redundant with return-lag features. Its incremental value is more likely as a regime or interaction input in trees than as a standalone predictor.

### Gaps
- I found no peer-reviewed study that isolates MACD's incremental cross-sectional IC on Indian stocks.
- I could not verify the MDPI daily-technical-signals paper's net-of-cost results.

## 4. Standard practitioner frameworks/tools (Qlib, Alpha158/360, LightGBM baselines)

### Takeaway
Microsoft Qlib is the de facto open-source reference stack. It provides a data layer, the Alpha158 and Alpha360 handlers, about 20 benchmark models, and a TopkDropout backtest with Chinese cost assumptions. Its LightGBM-on-Alpha158 run is the standard baseline: IC about 0.045, annualized excess return about 9% after costs, IR about 1.0, CSI300 2017 to Aug 2020. Reproductions on later windows show weaker or negative excess returns.

### Cited Findings
- LightGBM Alpha158 workflow:
  - Market CSI300, benchmark SH000300.
  - Train 2008–2014, validation 2015–2016, test 2017-01-01 to 2020-08-01.
  - LGBModel with MSE loss: learning rate 0.2, max_depth 8, num_leaves 210, lambda_l1 about 205.7, colsample_bytree about 0.89.
  - TopkDropoutStrategy: hold the top 50 and replace the worst 5 each day.
  - Costs: open 5 bps, close 15 bps (includes China stamp duty), minimum cost 5 CNY. The limit-up/down threshold of 9.5% blocks untradable fills. Deals are at the close.
  - — [Qlib config](https://raw.githubusercontent.com/microsoft/qlib/main/examples/benchmarks/LightGBM/workflow_config_lightgbm_Alpha158.yaml)
- Benchmark tables (20 random seeds) for both feature sets are in the Section 2 findings above. — [Qlib README](https://github.com/microsoft/qlib/blob/main/examples/benchmarks/README.md)
- Metric definitions:
  - IC: daily cross-sectional Pearson correlation between score and label. Rank IC is the Spearman version.
  - ICIR: mean IC divided by its standard deviation.
  - Annualized excess return: (portfolio − benchmark − costs) × 252.
  - IR: mean daily excess return divided by its standard deviation, × √252.
  - — [arXiv 2608.28632 (describes the Qlib metrics)](https://arxiv.org/pdf/2608.28632)
- Recent LLM and agent alpha-mining papers (2023–2026) still use Qlib CSI300/CSI500 with Alpha158 plus LightGBM as the baseline to beat. — [arXiv 2306.12964](https://arxiv.org/pdf/2306.12964); [arXiv 2602.11917](https://arxiv.org/pdf/2602.11917)

### Inferences
- Porting Qlib to NSE is practical. Its data format accepts any daily OHLCV, and Alpha158 is pure price and volume, so it fits a MACD trader's existing data. Several settings must change:
  - Costs: India's STT, stamp duty and exchange fees. Delivery is roughly 10–15 bps a side all-in for retail, and intraday or F&O differ. Check current rates; they were not researched here.
  - Circuit limits: per-stock bands of 2/5/10/20% instead of 9.5%.
  - Universe: NIFTY 100/200/500 or the F&O stock list, with point-in-time membership.
  - Label: the default `Ref($close,-2)/Ref($close,-1)-1` (next-day open-to-next-close style) assumes acting on the following session.
- The Qlib baseline IR of about 1.0 was earned in one 3.5-year China window. Treat it as an upper-range reference, not an expectation.

### Gaps
- I found no public Qlib benchmark on Indian data.
- I did not fetch the official Alpha158 label definition from the Qlib source. The label formula above is from memory of the Qlib handler and should be checked in `qlib/contrib/data/handler.py`.

## 5. Typical pitfalls (label leakage, survivorship, overfitting) and setup conventions

### Takeaway
The literature's best practices:
- Walk-forward training: expanding or rolling windows, refit yearly in academia or more often in practice, with a separate validation block.
- Point-in-time universes and reporting lags.
- Cross-sectionally ranked or normalized features and targets. The target is next-period return relative to the cross-section: median or excess return, or a rank or classification label.
- Portfolio evaluation on value-weighted, microcap-excluded universes, after costs, and split into pre- and post-2004/2010 sub-periods.

Most failed results come from leakage, survivorship, microcap or illiquid profits, ignoring costs and turnover, and tuning on the test period.

### Cited Findings
- Look-ahead control: GKX lag monthly characteristics at least 1 month, quarterly at least 4 months and annual at least 6 months. They avoid standard K-fold cross-validation "to maintain the temporal ordering". — [GKX NBER WP](https://www.nber.org/papers/w25398.pdf)
- Survivorship: Krauss et al. reconstruct historical S&P 500 membership "after elimination of survivor bias". The Indian study (Lalwani & Meshram) uses a "survivorship bias free sample" of all firms on major Indian exchanges, 1994–2019. — [Krauss et al.](https://www.econstor.eu/bitstream/10419/130166/1/856307327.pdf); [IDEAS / Applied Economics 2022](https://ideas.repec.org/a/taf/applec/v54y2022i16p1814-1828.html)
- Targets and ranking setups:
  - GKX: regression on excess return (l2 or Huber loss) with decile sorts.
  - Krauss et al.: binary above-cross-sectional-median classification, trading the top and bottom k.
  - Qlib: regression on next-day return with MSE loss, Top-50/Dropout-5 daily rebalance.
  - Ranking losses are an option (the MDPI study compares four XGBoost objectives).
  - — [GKX](https://www.nber.org/papers/w25398.pdf); [Krauss](https://www.econstor.eu/bitstream/10419/130166/1/856307327.pdf); [Qlib config](https://raw.githubusercontent.com/microsoft/qlib/main/examples/benchmarks/LightGBM/workflow_config_lightgbm_Alpha158.yaml); [MDPI](https://www.mdpi.com/2227-7099/14/9/393)
- Microcap and illiquidity: profits fall 62% without microcaps. — [Avramov et al.](https://papers.ssrn.com/abstract=3450322). The equal-weighted Sharpe falls from 2.45 to 1.69 below the NYSE 20th percentile. — [GKX](https://www.nber.org/papers/w25398.pdf)
- Turnover and costs: ML long-short portfolios turn over 110–130% or more per month. — [GKX](https://www.nber.org/papers/w25398.pdf). Costs cut 13–40% of returns, and simple mitigation tweaks mostly backfire. — [Azevedo et al. via Quantpedia](https://quantpedia.com/the-expected-returns-of-machine-learning-strategies/). Daily stat-arb Sharpe falls from 4.7 to 1.8. — [Krauss et al.](https://www.econstor.eu/bitstream/10419/130166/1/856307327.pdf)
- Overfitting and naive models: unregularized OLS on 920 features gives an out-of-sample R² of −3.46%, worse than predicting zero. — [GKX](https://www.nber.org/papers/w25398.pdf). Random forest plus technical indicators overfits and generalizes poorly. — [arXiv 2412.15448](https://arxiv.org/html/2412.15448v1)
- Alpha decay: Krauss's after-cost Sharpe fell from 6.7 (1992–2001) to 0.51 (2001–2008). — [Krauss et al.](https://www.econstor.eu/bitstream/10419/130166/1/856307327.pdf). LSTM profits were about zero after 2010. — [Fischer & Krauss](https://ideas.repec.org/a/eee/ejores/v270y2018i2p654-669.html). Large-cap ML alpha is insignificant over 2001–2020. — [Quantpedia on Cakici et al.](https://quantpedia.com/decreasing-returns-of-machine-learning-strategies/)
- Market-microstructure realism: Qlib blocks fills at limit-up/limit-down (9.5% threshold) and charges a minimum cost. — [Qlib config](https://raw.githubusercontent.com/microsoft/qlib/main/examples/benchmarks/LightGBM/workflow_config_lightgbm_Alpha158.yaml)
- Seed variance: Qlib NN results vary across 20 seeds. For example, the MLP's IR is 1.14 ± 0.23 and the LSTM's 0.56 ± 0.46, while GBDT results are deterministic (±0.00). — [Qlib README](https://github.com/microsoft/qlib/blob/main/examples/benchmarks/README.md)

### Inferences
- Indian-market specifics to guard against:
  - Survivorship in NSE symbol lists, because of delistings, mergers and symbol changes.
  - Corporate-action adjustment errors, which affect lagged-return features most.
  - Circuit-limit fills.
  - Point-in-time F&O eligibility, since the F&O stock list changes.
  - Labels that use same-day close when the signal is acted on at the 14:45 close (intraday leakage).
  - STT and stamp duty in the cost model.
- Report results the way the literature does: rank IC and ICIR, then decile spread or top-k returns after costs, then sub-period splits. Show both pre-2020 and post-2020 to check India-specific decay.
- With GBDTs, single-seed results are deterministic but still sensitive to hyperparameters. With MLPs, average over several seeds before trusting any improvement.

### Gaps
- India-specific evidence is thin. Lalwani & Meshram (Applied Economics 2022; 35 characteristics, all listed Indian firms 1994–2019; ENet, RF, XGBoost and NNs) report "substantial improvement in forecast accuracy" over OLS and "significant returns", with 14 of 35 predictors having t > 3 in Fama-MacBeth regressions. I could not access the R², Sharpe, after-cost or size-filtered numbers (ResearchGate blocked).
- The Hanauer & Kalsbach EM sample (32 countries) very likely includes India, but I could not confirm India-level results or after-cost figures.
- I found no study on ML for NSE F&O-eligible stocks specifically, and none combining ML ranks with an options overlay.
- McLean & Pontiff-style post-publication decay figures for anomalies (the about 26% and 58% declines) were not re-verified in this session.

Sources consulted that were not directly cited: [Lalwani & Meshram on ResearchGate](https://www.researchgate.net/publication/354819735_The_cross-section_of_Indian_stock_returns_evidence_using_machine_learning); [Machine learning goes global, JEDC 2023](https://www.sciencedirect.com/science/article/abs/pii/S0165188923001318) (403); [IIMB "Global Edition" PDF](https://www.iimb.ac.in/sites/default/files/inline-files/Empirical%20Asset-Pricing-Machine-Learning.pdf) (403).
