# Deep Learning, Time-Series Foundation Models, Graph Models and Deep RL for Stock Picking and Price Forecasting

Scope note: research run 3 Oct 2026. Emphasis on 2022–2026 evidence; older items are flagged "(older)". End-user context: intraday/daily MACD-based paper trading on NSE equities plus Nifty/BankNifty options.

## Q1. Which deep models show robust out-of-sample gains for cross-sectional stock selection vs. price-direction prediction? (Qlib benchmarks, Jiang/Kelly/Xiu CNN-on-charts)

### Takeaway
On the most widely used open benchmark (Qlib, CSI300, 2017–2020 test), gradient-boosted trees on hand-crafted factors are as good as or better than most deep nets. Deep sequence and graph models (HIST, TRA, IGMTF, GATs, GRU) only beat GBDT when given raw price/volume inputs (Alpha360), and even then by small IC margins (about 0.05 vs 0.04). The strongest peer-reviewed deep-learning result is the Jiang–Kelly–Xiu CNN on price-chart images (JF 2023). It is a short-horizon (1-week) cross-sectional ranking signal: very strong gross in equal-weight portfolios, much weaker in value-weight, and very high turnover. When the U.S.-trained model was applied directly to India, it failed (negative Sharpe), but a model retrained on Indian data did work. Deep models help more with cross-sectional ranking (IC of about 0.03–0.06) than with single-asset direction calls (accuracy around 51%).

### Cited Findings
**Qlib benchmark (Microsoft), CSI300, mean ± std over 20 seeds**
- Setup: train 2008–2014, valid 2015–2016, test 2017-01-01 to 2020-08-01 on CSI300, benchmark SH000300. The strategy is TopkDropout (hold the top 50, drop 5 per day), with open cost 0.05%, close cost 0.15% and minimum cost 5. — [Qlib LightGBM Alpha158 config](https://github.com/microsoft/qlib/blob/main/examples/benchmarks/LightGBM/workflow_config_lightgbm_Alpha158.yaml)
- **Alpha158 (hand-engineered factors):**
  - Gradient boosting and simple models: DoubleEnsemble IC 0.0521 / Rank IC 0.0502 / ann. excess return 11.6% / IR 1.34; LightGBM IC 0.0448 / Rank IC 0.0469 / 9.0% / IR 1.02; XGBoost IC 0.0498 / 7.8% / IR 0.91; Linear IC 0.0397 / 6.9% / IR 0.92; MLP IC 0.0376 / 9.0% / IR 1.14.
  - Deep models: LSTM IC 0.0318 / 3.8% / IR 0.56; GRU IC 0.0315 / 3.4% / IR 0.52; Transformer IC 0.0264 / 2.7% / IR 0.40; TCN IC 0.0279 / IR 0.41; TabNet IC 0.0204; GATs IC 0.0349 / IR 0.73; TRA IC 0.0440 / 7.2% / IR 1.08; TFT IC 0.0358, but Rank IC only 0.0116 and max drawdown -18.2%.
  - Reading: on engineered factors, every deep model except TRA trails LightGBM, and a linear model beats LSTM, GRU and Transformer. — [Qlib benchmarks README](https://github.com/microsoft/qlib/blob/main/examples/benchmarks/README.md)
- **Alpha360 (raw 60-day price/volume):**
  - Graph and sequence models: HIST IC 0.0522 / Rank IC 0.0667 / 9.9% / IR 1.37; IGMTF IC 0.0480 / IR 1.35; TRA IC 0.0485 / IR 1.28; TCTS IC 0.0508 / IR 1.23; GATs IC 0.0476 / IR 1.11; GRU IC 0.0493 / IR 0.97; LSTM IC 0.0448 / IR 0.90.
  - Gradient boosting on the same raw inputs: LightGBM IC 0.0400 / 5.6% / IR 0.76.
  - Failures: Transformer IC 0.0114 / -2.7% / IR -0.34; TabNet -3.7%; KRNN -4.7%; Sandwich roughly 0%. — [Qlib benchmarks README](https://github.com/microsoft/qlib/blob/main/examples/benchmarks/README.md)
- A 2025 LLM-alpha-mining paper re-ran baselines on CSI300 at a 10-day horizon and reported LightGBM IC 0.0269 / Rank IC 0.0412 vs Transformer IC -0.0090 / Rank IC -0.0022. — [arXiv 2511.18850 (search snippet)](https://arxiv.org/pdf/2511.18850)

**Jiang, Kelly & Xiu, "(Re-)Imag(in)ing Price Trends," Journal of Finance 78(6), 2023**
- Data and design: U.S. CRSP 1993–2019. The CNN is trained on 1993–2000 and then held fixed for the 2001–2019 out-of-sample test. Inputs are OHLC bar images with moving average and volume covering 5, 20 or 60 days, and the target is the probability that the 5-, 20- or 60-day return is positive. — [JF paper PDF (Yale)](https://economics.yale.edu/sites/default/files/2023-11/The%20Journal%20of%20Finance%20-%202023%20-%20JIANG%20-%20Re%25E2%2580%2590%20Imag%20in%20ing%20Price%20Trends_0.pdf)
- **Weekly-rebalanced decile long-short (H-L), gross:**
  - Equal-weight Sharpe 7.2 / 6.8 / 4.9 for 5-, 20- and 60-day images, vs MOM 0.1, STR 1.8, WSTR 2.8 and TREND 2.9.
  - Value-weight Sharpe 1.4–1.7 for CNN vs 0.8 for the best benchmark (WSTR). — [same](https://economics.yale.edu/sites/default/files/2023-11/The%20Journal%20of%20Finance%20-%202023%20-%20JIANG%20-%20Re%25E2%2580%2590%20Imag%20in%20ing%20Price%20Trends_0.pdf)
- **Turnover and costs:**
  - Turnover is about 670–760% per month for the weekly CNN strategies, similar to WSTR. The authors say the weekly strategy is "mostly accessible to investors who behave as market makers."
  - Net-of-cost equal-weight Sharpe (10/20 bp costs) is up to 4.0 weekly, 1.5 monthly and 0.9 quarterly.
  - Restricted to the largest 500 stocks, Sharpe stays above 1.0 for both equal- and value-weight. — [same](https://economics.yale.edu/sites/default/files/2023-11/The%20Journal%20of%20Finance%20-%202023%20-%20JIANG%20-%20Re%25E2%2580%2590%20Imag%20in%20ing%20Price%20Trends_0.pdf)
- **Longer holding periods:** monthly (I5/R20) equal-weight H-L Sharpe is 2.4 and quarterly (I5/R60) is 1.3, vs 0.7 for quarterly WSTR. Most of the monthly return arrives in the first 5 days; the Sharpe for days 6–20 is 0.4–1.2. — [same](https://economics.yale.edu/sites/default/files/2023-11/The%20Journal%20of%20Finance%20-%202023%20-%20JIANG%20-%20Re%25E2%2580%2590%20Imag%20in%20ing%20Price%20Trends_0.pdf)
- **International transfer (26 markets, I5/R5):**
  - Applying the U.S.-trained CNN directly beats locally retrained models in 21 of 26 markets: average equal-weight Sharpe 3.6 vs 2.3.
  - **India (about 1,861 stocks): locally retrained equal-weight Sharpe 2.52 (value-weight 0.67), but direct U.S. transfer gives -1.30 (value-weight -1.42).** China shows the same pattern (retrain 2.26, transfer -2.30). — [same, Table X](https://economics.yale.edu/sites/default/files/2023-11/The%20Journal%20of%20Finance%20-%202023%20-%20JIANG%20-%20Re%25E2%2580%2590%20Imag%20in%20ing%20Price%20Trends_0.pdf)
- **Image vs numeric input:** a logistic model fed image-scaled data reaches equal-weight Sharpe 2.0 (I20/R20), but only 0.4 with cumulative-return-scaled inputs. Most of the gain comes from normalising each window by its own high–low range, not from depth. — [same](https://economics.yale.edu/sites/default/files/2023-11/The%20Journal%20of%20Finance%20-%202023%20-%20JIANG%20-%20Re%25E2%2580%2590%20Imag%20in%20ing%20Price%20Trends_0.pdf)

**Large-panel daily return prediction (global)**
- Rahimikia, Ni & Wang (Nov 2025) used about 2 billion daily observations from 94 countries (1990–2023; U.S. test with a 512-day window).
  - CatBoost: R² -0.10%, directional accuracy 51.16%, long-short Sharpe 6.79.
  - Zero-shot Chronos-large: R² -1.37%, Sharpe 0.43. Zero-shot TimesFM-500M: R² -2.80%, Sharpe -0.03.
  - Single-stock direction accuracy is about 51% even for the best models, yet the cross-sectional sort is profitable (gross). — [arXiv 2511.18578](https://arxiv.org/html/2511.18578v1)

**Recent cross-sectional rankers**
- A 2026 deployment paper's LightGBM ranker (momentum and volatility features) has walk-forward Sharpe 2.73, falling to 1.91 from 2024 onward. — [arXiv 2603.13252 (search snippet)](https://arxiv.org/pdf/2603.13252)

### Inferences
- Value comes from cross-sectional ranking (IC of about 0.03–0.06, top-k portfolios), not from predicting a single stock's direction. Single-name direction accuracy sits at about 50–52% across the large studies above.
- **Model choice:**
  - On engineered features (similar to MACD, RSI or momentum indicators), GBDT and even linear models match or beat LSTM, GRU and Transformer. Qlib's Alpha158 results are the clearest public evidence.
  - Deep models only win when they must learn features from raw sequences (Alpha360). Even then, the best graph/sequence models (HIST, TRA) add roughly 0.005–0.01 IC over LightGBM.
- **JKX CNN result:** strong, but concentrated at the weekly horizon, in equal-weight/smaller names, and with very high turnover. For NSE, the paper's own Table X shows that training on local data is required.
  - Indian costs (STT, exchange fees, impact costs in mid-caps) would erode a weekly 700%-turnover strategy heavily. This is an inference; India-specific net numbers are not given in the paper.
- Qlib numbers are from a single market (China A-shares, 2017–2020) under a fixed cost model. Treat them as relative rankings of architectures, not as expected live alpha.

### Gaps
- Gu, Kelly & Xiu (2020, RFS, older) neural-net asset-pricing numbers were not re-verified in this session.
- No rigorous, peer-reviewed NSE cross-sectional benchmark (Qlib-style) comparing deep models vs GBDT was found. The India-specific DL papers found (LSTM/CNN on HDFC, TCS, Nifty; LSTM on 180 NSE stocks) report MAPE/RMSE on price levels, which say little about tradability. — [arXiv 2407.01572](https://arxiv.org/abs/2407.01572); [MFI 2026 (Charithra C. M.)](https://businessperspectives.org/images/pdf/applications/publishing/templates/article/assets/24361/MFI_2026_02_Charithra%20C.%20M..pdf)
- Qlib tables do not report post-2020 performance, so no evidence was found on how these rankings held up in 2021–2026.

## Q2. Graph neural networks for stock relations

### Takeaway
On Qlib's raw-feature benchmark, graph and relation models (HIST, IGMTF, GATs) are among the top performers. The broader GNN literature, however, has well-documented benchmark leakage, tuning on test data and reproducibility problems, and only about 24% of financial GNN papers release code. No independent leakage-free evidence was found that GNN gains survive costs out of sample.

### Cited Findings
- **Qlib Alpha360:** HIST (concept/industry graph) reaches the best Rank IC of 0.0667 and IR 1.37, IGMTF IR 1.35, and GATs IR 1.11, vs LightGBM IR 0.76. On Alpha158, GATs (IR 0.73) trails LightGBM (1.02). — [Qlib benchmarks README](https://github.com/microsoft/qlib/blob/main/examples/benchmarks/README.md)
- A review of financial GNN methods found only about 24% of papers release code, which makes reproduction hard; stock movement prediction is usually framed as node classification. — [Wang et al., GNN in finance review, arXiv 2111.15367 (older, 2021)](https://arxiv.org/pdf/2111.15367)
- **General GNN benchmarking critiques:**
  - A well-tuned MLP can beat many reported GNN results because graph structure leaks into node features. — [arXiv 2412.06173](https://arxiv.org/pdf/2412.06173)
  - Heterogeneous-GNN studies show data leakage and tuning on test sets. — [arXiv 2112.14936](https://arxiv.org/pdf/2112.14936)
  - Single-split evaluations are fragile. — [Shchur et al., arXiv 1811.05868 (older)](https://arxiv.org/pdf/1811.05868)
  - On the Elliptic financial graph, a 39.5-point F1 gap was traced to training-time exposure to test-period adjacency, a form of look-ahead. — [search result, arXiv 2604.19514 via HF](https://huggingface.co/papers/2604.19514)
- Relation graphs used in the stock literature include industry membership, supply chain, shareholders, price comovement and dynamic correlation. — [RankGLU, arXiv 2606.08930](https://arxiv.org/pdf/2606.08930)

### Inferences
- **Look-ahead risk:** graphs built from price comovement or correlation must be estimated only on past data. Graphs built from current industry or concept membership can carry survivorship or look-ahead bias.
- **NSE fit:** sector and industry relations are easy to obtain (NSE sector indices), but the evidence does not justify GNN complexity over a GBDT with sector-relative features for a small paper-trading system.

### Gaps
- No NSE-specific GNN study with a credible cost-adjusted backtest was found.
- No leakage-audited replication of HIST, THGNN or RSR-style stock GNNs on a post-2021 sample was found.

## Q3. Zero-shot time-series foundation models (Chronos, TimesFM, Moirai, Lag-Llama, TimeGPT) and finance-specific FMs (Kronos, FinCast) on financial returns (2024–2026)

### Takeaway
Generic TSFMs used zero-shot are at best about as good as a random walk on returns. Their gains are "small and sparse" and rarely statistically significant, and on large panels they lose clearly to CatBoost/LightGBM (negative R², Sharpe around 0 to 0.4 vs 6.8 for CatBoost gross). Finance-specific pretraining (Kronos; TSFMs retrained from scratch on returns) recovers much of the gap. The best published Kronos price-forecast IC (about 0.04) is similar in size to Qlib's LightGBM. For volatility forecasting, TSFMs are roughly level with HAR models, and an ensemble is most robust.

### Cited Findings
- **Noguer i Alonso & Pereira Franklin (Jun 2026):**
  - Models: TimeGPT, TimesFM-2.5, Moirai-2.0, Chronos and Chronos-2 (zero-shot) vs NBEATS, NHITS, PatchTST, iTransformer and KAN, on AAPL, AMZN, GOOG, JPM and META daily returns. Rolling origin, evaluation from Aug 2024 to Feb 2026, 20-day horizon.
  - Pretrained models took 8 of 10 task wins, but the Diebold–Mariano test rejected equal accuracy only for Chronos on AMZN and Moirai-2.0 on GOOG; iTransformer won both META tasks.
  - Conclusion: TSFMs are "not universal engines for statistically reliable alpha generation," with gains over the random walk "small and sparse." — [arXiv 2606.27100](https://arxiv.org/abs/2606.27100)
- **Rahimikia, Ni & Wang (Nov 2025), 94 countries, daily excess returns:**
  - Zero-shot Chronos-large: R² -1.37%, Sharpe 0.43. Zero-shot TimesFM-500M: R² -2.80%, Sharpe -0.03. CatBoost: R² -0.10%, accuracy 51.16%, Sharpe 6.79.
  - Pretraining from scratch on financial data helps: Chronos-small Sharpe 5.42, TimesFM-20M Sharpe 3.66. A tuned, scaled (global data plus factors) Chronos-small reaches accuracy 51.74%, 41.9% annualised return and Sharpe 6.78.
  - Conclusion: "generic time series pre-training does not directly transfer to financial domains." India is in the dataset, but no India-only result is reported. — [arXiv 2511.18578](https://arxiv.org/html/2511.18578v1)
- **ISD 2025 study:** zero-shot Chronos was statistically comparable to the best traditional forecasters on accuracy, but its trading performance lagged the top benchmarks, and fine-tuned Chronos was worse than zero-shot. — [ISD 2025 paper](https://isd2025.fon.bg.ac.rs/wp-content/uploads/ISD_2025_camera_ready_265.pdf); [arXiv 2507.07296](https://arxiv.org/abs/2507.07296v1)
- **Same 2025 line of work:** zero-shot TTM beat naive benchmarks for volatility and equity-spread forecasting, the places where TSFMs help. — [arXiv 2507.07296 (search snippet)](https://arxiv.org/abs/2507.07296v1)
- **Brini (Jul 2026), realized volatility, 50 assets (VOLARE), 9 zero-shot TSFMs vs 8 HAR/log-HAR models:**
  - "Foundation models do not deliver a uniform gain." Only TTM consistently beat log-HAR, and narrowly.
  - An equal-weight TTM + log-HAR ensemble was most robust (in the Model Confidence Set for 98–100% of assets).
  - Short-horizon gains mostly reflect better scaling, not better dynamics. — [arXiv 2607.05291](https://arxiv.org/abs/2607.05291)
- **Kronos (Tsinghua; arXiv Aug 2025; NeurIPS 2025 / AAAI listings):**
  - Pretrained on more than 12 billion K-line (OHLCV) records from 45 exchanges at 7 frequencies, with a discrete tokenizer and autoregressive Transformer. Sizes: 24.7M, 102.3M and 499.2M parameters.
  - Claims +93% RankIC over the leading TSFM and +87% over the best non-pretrained baseline (iTransformer), 9% lower volatility MAE, and 22% better synthetic-data fidelity. Kronos-small price IC is 0.0431 and RankIC 0.0254 (as extracted).
  - The backtest is on CSI300/CSI800 daily data: 90-day lookback, 10-day horizon, top-50/200, 0.15% cost, minimum 5-day hold. Code and weights are public. — [arXiv 2508.02739](https://arxiv.org/html/2508.02739v1); [NeurIPS 2025 page](https://neurips.cc/virtual/2025/132504)
- **Chronos-2 (Oct 2025):** adds multivariate and covariate support, and leads GIFT-Eval (97 tasks), but that is a general benchmark, not a financial-returns one. — [arXiv 2510.15821](https://arxiv.org/pdf/2510.15821)
- **FinCast (Aug 2025)** is another finance-specific foundation model. — [arXiv 2508.19609](https://arxiv.org/pdf/2508.19609)

### Inferences
- **Zero-shot TSFMs on Nifty or stock returns:** expect roughly random-walk accuracy. They are not a replacement for MACD signal logic.
- **Where they help:** volatility, volume and spread forecasting, as inputs for option sizing or strike selection. They are on par with HAR, and an ensemble with HAR is safest.
- **Kronos:** the most relevant FM for OHLCV/K-line data and the one to try first if an FM is tested. Its headline gains are relative to weak TSFM baselines, and its absolute IC is comparable to a LightGBM on engineered factors. It is also self-reported by the authors, with no independent replication found.
- **Contamination risk:** public pretrained models trained on web or market data through 2024–2025 may have seen the test period. Any backtest on historical NSE data would be contaminated unless the evaluation window is after the model's training cutoff.

### Gaps
- No Lag-Llama-specific financial-returns study was found in this session.
- No independent replication of Kronos was found.
- No TSFM study on NSE/Indian intraday data was found.

## Q4. Deep RL trading and portfolio agents (FinRL etc.): realistic performance after costs and critiques

### Takeaway
Deep RL trading results are highly sensitive to cost and market-impact modelling. A 2026 study shows that algorithm rankings and out-of-sample Sharpe ratios flip when the fixed 10 bp cost is replaced with an Almgren–Chriss/square-root impact model. Systematic reviews find most RL trading papers assume no costs, no spread and no liquidity limits. No credible evidence was found of DRL agents beating buy-and-hold or simple momentum after realistic costs.

### Cited Findings
- **Riera Abbade & Reali Costa (Mar/Apr 2026):**
  - They built impact-aware Gymnasium environments as an extension to FinRL-Meta, because "most open-source backtesting environments assume negligible or fixed transaction costs, causing agents to learn trading behaviors that fail under realistic execution."
  - Switching to Almgren–Chriss costs changed DDPG's out-of-sample Sharpe from -2.1 to 0.3 (margin trading) and SAC's from -0.5 to -1.2. Learned behaviour changed from about $200k to $8k in daily costs and from 19% to 1% turnover. Hyperparameter tuning cut costs by up to 82%. — [arXiv 2603.29086](https://arxiv.org/abs/2603.29086v2)
- **Systematic review (older, 2019):** all recent RL trading articles reviewed had unrealistic assumptions (no transaction costs, liquidity or bid-ask issues), and transaction costs significantly affected RL profitability relative to baselines. — [Data 4(3):110 via Crossref](https://api.crossref.org/works/10.3390%2FDATA4030110)
- **Dueling DQN study (2025):** models were positive without commissions but deteriorated sharply with commissions, which the authors attribute to overfitting short-term noise. — [arXiv 2504.11601](https://arxiv.org/pdf/2504.11601)
- **FinRL Contests (2023–2025):** 200+ participants and 85 teams in 2025. Tasks covered stock trading, order execution, crypto and LLM-signal (FinRL-DeepSeek) trading. The second-place 2025 Task-1 entry (DAPO) is reported on the NASDAQ-100, but no standardised cost-adjusted leaderboard vs buy-and-hold was found. — [FinRL Contest 2025 overview](https://finrl-contest.readthedocs.io/en/latest/finrl2025/overview.html); [arXiv 2505.06408](https://arxiv.org/pdf/2505.06408); [arXiv 2504.02281](https://arxiv.org/html/2504.02281v3)
- **Zhang, Zohren & Roberts (older, 2019):** deep RL for futures trading with volatility scaling, often cited as a positive result for time-series momentum-style DRL. — [arXiv 1911.10107](https://arxiv.org/pdf/1911.10107)

### Inferences
- **Most valuable use for this system:** realistic cost and impact modelling, plus "doing nothing" (low turnover) as a learned behaviour. The 19%→1% turnover result shows naive DRL overtrades under flat-cost simulators.
- **Indian options:** Nifty/BankNifty premiums carry STT, exchange charges and wide spreads in far-OTM strikes. RL agents trained in flat-cost simulators would likely overtrade. Rule-based MACD with fixed exits is a more defensible baseline.

### Gaps
- No peer-reviewed DRL study was found showing robust, cost-adjusted outperformance on Indian equities or index options.
- No published FinRL contest results comparing winners to buy-and-hold on held-out data were found.

## Q5. Intraday/high-frequency (limit order book models like DeepLOB) and relevance to intraday index options

### Takeaway
LOB deep models report high in-sample/benchmark accuracy on FI-2010, but a 2023–2024 benchmark (LOBCAST) found that results often do not reproduce. Every model dropped sharply on unseen stocks and periods (F1 about 48–61%), and profitability was not established. Directional signal at tick horizons is weak unless volume/order-flow imbalance is used. For Nifty/BankNifty options, LOB-based mid-price direction models are not shown to transfer. The more defensible deep-learning use is volatility forecasting.

### Cited Findings
- **LOBCAST (Prata et al., 2023; Springer AI Review 2024), 15 models:**
  - Reproduced F1 on FI-2010 differed sharply from the claimed values: TransLOB 87.3% claimed vs 59.4% reproduced; ATNBoF 67.1% vs 40.9%; BiN-CTABL 80.1% vs 82.6% (the best model).
  - On unseen LOBSTER stocks (2021, 2022), F1 was 48–61%. BiN-CTABL fell about 19.6% from FI-2010. Trading simulations showed no guaranteed profitability.
  - Authors' conclusion: models overfit FI-2010, and current DL "lack[s] the sophistication needed for practical trading." — [arXiv 2308.01915](https://arxiv.org/html/2308.01915v2); [Springer AI Review](https://link.springer.com/article/10.1007/s10462-024-10715-4)
- DeepLOB loses more than 25% accuracy under perturbations to both sides of the book. — [LOBCAST/benchmark, search snippet](https://arxiv.org/abs/2308.01915v1)
- A 2025 LOB representation-learning benchmark finds that specialised LOB models like DeepLOB can beat generic models on some downstream tasks. — [arXiv 2505.02139](https://arxiv.org/pdf/2505.02139)
- **Lee (Sep 2024):** "directional prediction performance is not substantial" from price data alone. Volume imbalance materially improves it, and badly defined target price processes can leak past information. — [arXiv 2409.14157](https://arxiv.org/abs/2409.14157)
- DeepLOB architecture: CNN plus inception plus LSTM over 10-level LOB snapshots, using the previous 100 events (older, 2018–2019). — [arXiv 1808.03668](https://ar5iv.arxiv.org/html/1808.03668)
- **TSFMs for volatility:** on par with HAR at short horizons, with only narrow gains (see Q3). — [arXiv 2607.05291](https://arxiv.org/abs/2607.05291)

### Inferences
- **Data access:** the user's stack (Fyers-style candles, 1-minute and daily) does not include full-depth LOB. DeepLOB-class models need tick-level L2/L3 data, which NSE provides only via paid feeds (inference).
- **Possible uses of deep learning intraday for Nifty/BankNifty options:**
  - Short-horizon realized-volatility forecasting (HAR-style plus TSFM ensemble) to choose between buying and selling premium, or to set stops.
  - Order-flow-imbalance features where data exists.
  - Directional deep models over 1-minute bars are unlikely to beat a simple trend rule after option spreads and theta.
- **Label leakage:** results built on mid-price labels with smoothing windows (as in FI-2010 conventions) can embed future information. Any in-house intraday ML labelling should use strictly forward-only returns.

### Gaps
- No study was found applying DeepLOB, LOBCAST models or TSFMs to Nifty/BankNifty futures or options order books.
- The original DeepLOB headline accuracies (FI-2010, LSE) were not re-verified in this session.

## Q6. Known failure modes: non-stationarity, low signal-to-noise, overfitting, look-ahead bias in benchmarks

### Takeaway
The recurring pattern is that deep models look strong on fixed historical benchmarks and degrade on new data. Causes include regime shift (LightGBM ranker Sharpe 2.73 → 1.91 after 2024; U.S.-trained CNN negative in India and China), irreproducible benchmark claims (TransLOB 87%→59%), leakage (graph/adjacency look-ahead, smoothed labels, possible pretraining contamination in foundation models) and unrealistic cost modelling (DRL). The signal is tiny: R² around 0 or negative and direction accuracy around 51%, so seed variance and hyperparameter search can swamp the true edge.

### Cited Findings
- **Low signal-to-noise:**
  - Best large-panel daily direction accuracy is about 51.2–51.7%, with R² of -0.10% (CatBoost) and -0.59% (Chronos pretrained from scratch). — [arXiv 2511.18578](https://arxiv.org/html/2511.18578v1)
  - Returns forecasting is hard because of "low signal-to-noise ratios, structural breaks, heavy tails, and weak persistence." — [arXiv 2606.27100](https://arxiv.org/abs/2606.27100)
- **Non-stationarity / decay:**
  - LightGBM ranker Sharpe fell from 2.73 (full walk-forward) to 1.91 (2024 onward). — [arXiv 2603.13252](https://arxiv.org/pdf/2603.13252)
  - Rahimikia et al. report that finance-pretrained models degrade more slowly over time than off-the-shelf ones. — [arXiv 2511.18578](https://arxiv.org/html/2511.18578v1)
- **Cross-market non-transfer:** the U.S.-trained CNN gives India EW Sharpe -1.30 vs 2.52 when retrained locally, and China -2.30 vs 2.26. — [JKX 2023, Table X](https://economics.yale.edu/sites/default/files/2023-11/The%20Journal%20of%20Finance%20-%202023%20-%20JIANG%20-%20Re%25E2%2580%2590%20Imag%20in%20ing%20Price%20Trends_0.pdf)
- **Seed variance:** Qlib deep models show large across-seed standard deviations in IR (for example LSTM 0.56 ± 0.46, Transformer 0.40 ± 0.26 on Alpha158). GBDT shows about ±0.00 because it is deterministic. — [Qlib benchmarks README](https://github.com/microsoft/qlib/blob/main/examples/benchmarks/README.md)
- **Irreproducibility:** LOB model F1 claims failed to reproduce, and all models dropped on new data. — [arXiv 2308.01915](https://arxiv.org/html/2308.01915v2)
- **Leakage in graph benchmarks:** structure leaking into features, test-set tuning, and test-period adjacency exposure (39.5-point F1 gap). — [arXiv 2412.06173](https://arxiv.org/pdf/2412.06173); [arXiv 2112.14936](https://arxiv.org/pdf/2112.14936); [HF 2604.19514](https://huggingface.co/papers/2604.19514)
- **Label leakage:** badly defined target price processes can leak past information. — [arXiv 2409.14157](https://arxiv.org/abs/2409.14157)
- **Cost unrealism in DRL:** see Q4. — [arXiv 2603.29086](https://arxiv.org/abs/2603.29086v2)

### Inferences
- **Equal-weight vs value-weight:** equal-weight decile Sharpe ratios (JKX 7.2; CatBoost 6.8 in the global panel) are gross and dominated by small, illiquid names. For an NSE system, read value-weight or top-liquidity results (JKX value-weight 1.4–1.7, top-500 Sharpe above 1.0) as the realistic ceiling before Indian costs.
- **Practical guardrails for the user's MACD stack:**
  - Walk-forward evaluation only, with multiple seeds and a minimum detectable effect.
  - Report results against buy-and-hold Nifty plus simple momentum and reversal baselines.
  - Model actual NSE cost structure (STT, exchange fees, GST, stamp duty, slippage).
  - Treat any ML layer as a filter or ranker on top of MACD, benchmarked against a LightGBM on the same features before trying deep architectures.
- These are consistent with the user's prior internal finding (2 Oct study) that allocation and filters mattered more than exit tweaks. Deep models are most plausible as a cross-sectional ranker or filter, not as entry-timing replacements.

### Gaps
- No study was found quantifying how NSE-specific costs (STT on options, which rose in 2024–2025 per regulatory changes not verified here) change the net profitability of high-turnover ML strategies.
- No peer-reviewed 2024–2026 survey was fetched that tabulates deep-model performance after costs across markets. Survey-level claims here are assembled from individual primary studies.
