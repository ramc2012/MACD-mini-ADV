# LLMs and LLM-Based Agents for Stock Picking and Trading (research notes, compiled 3 Oct 2026)

Scope note: about 20 search/fetch calls. Primary sources are arXiv/SSRN/journal pages where possible. Media and blog sources are flagged as such. Findings carry their publication dates. Nothing here is investment advice.

## 1. What did the key studies find (returns, Sharpe, decay)?

### Takeaway
The strongest result is LLM-scored news predicting next-day returns (Lopez-Lira & Tang; Chen/Kelly/Xiu, RFS 2025). That signal decays sharply. GPT-4's news-drift Sharpe fell from 6.54 (Q4 2021) to 1.22 (Jan–May 2024), and the strategy stops being profitable at about 20 bps round-trip cost. The famous "GPT-4 beats analysts on financial statements" paper (Kim/Muhn/Nikolaev) was withdrawn in Feb 2025 over data inconsistencies, so it should not be relied on.

### Cited Findings
**Lopez-Lira & Tang, "Can ChatGPT Forecast Stock Price Movements?" (arXiv 2304.07619; latest version v6, Oct 2025)**
- Sample: Oct 2021–May 2024; 159,137 firm-headline-date observations; 4,123 companies; 12 models from GPT-1/BERT up to GPT-4. The sample is after the models' Sept 2021 knowledge cutoff. — [arXiv v6](https://arxiv.org/html/2304.07619v6)
- GPT-4 predicts the non-tradable initial reaction with a hit rate of about 93% overnight and 89% intraday. The tradable drift hit rate is only 58% overnight and 55% intraday. — [arXiv v6](https://arxiv.org/html/2304.07619v6)
- The GPT-4 overnight drift long-short earns 34 bps/day before costs, with annualized Sharpe 2.97 (overnight) and 2.63 (intraday). GPT-3.5 Sharpe is 1.66 and DistilBart-MNLI is 1.26. GPT-1, GPT-2 and BERT cannot forecast, so the ability grows with model size. — [arXiv v6](https://arxiv.org/html/2304.07619v6); [arXiv abstract](https://arxiv.org/abs/2304.07619v6)
- Cumulative return is about 700% at zero cost, about 300% at 5 bps round-trip and over 100% at 10 bps. It becomes unprofitable at 20 bps round-trip. — [arXiv v6](https://arxiv.org/html/2304.07619v6)
- Decay: Sharpe went 6.54 (Q4 2021), 3.68 (2022), 2.33 (2023), 1.22 (Jan–May 2024). The authors write that "strategy returns decline as LLM adoption rises, consistent with improved price efficiency." — [arXiv v6](https://arxiv.org/html/2304.07619v6); [arXiv abstract](https://arxiv.org/abs/2304.07619v6)
- The predictability sits mostly in small stocks and negative news. The short leg has Sharpe 2.01 (26 bps/day) and the long leg 0.78 (8 bps/day). — [arXiv v6](https://arxiv.org/html/2304.07619v6)

**Chen, Kelly & Xiu, "Expected Returns and Large Language Models" (Review of Financial Studies 38, 2025, pp. 3542–3579)**
- They extract contextualized embeddings of news text from LLMs (ChatGPT, LLaMA, among others) to predict returns. These beat technical signals such as past returns and also beat bag-of-words or word-vector NLP. The gains are largest for articles with negation or complex narratives. — [search summary of RFS abstract](https://acem.sjtu.edu.cn/academic/82735.html); [Oxford NLP-econ seminar abstract](https://nlpecofin.web.ox.ac.uk/node/3041271)
- Coverage is 16 international equity markets and news in 13 languages. Prices absorb news with a delay consistent with limits to arbitrage. A real-time "fresh news alert" strategy gives even higher Sharpe ratios. — [Oxford seminar abstract](https://nlpecofin.web.ox.ac.uk/node/3041271); [Tsinghua PBCSF seminar notice](https://www.pbcsf.tsinghua.edu.cn/info/1504/7668.htm)

**Kim, Muhn & Nikolaev, "Financial Statement Analysis with Large Language Models" (Chicago Booth WP, May 2024)**
- The original claim was that GPT-4, given anonymized, standardized statements and chain-of-thought prompts, beat the median analyst at forecasting the direction of earnings, matched a purpose-trained ANN, and produced higher Sharpe and alpha. — [arXiv abs v2](https://arxiv.org/abs/2407.17866v2); [Chicago Booth page](https://www.chicagobooth.edu/research/fama-miller/finance-research/funding/a-demand-system-approach-for-fixed-income/financial-statement-analysis-with-large-language-models)
- **The paper was withdrawn on 20 Feb 2025 (arXiv v3).** The arXiv withdrawal notice says "A co-author identified inconsistencies in the data and analyses while attempting to replicate past analyses... we have temporarily withdrawn the working paper." — [arXiv 2407.17866](https://arxiv.org/abs/2407.17866)

**Pelster & Val, "Can ChatGPT assist in picking stocks?" (Finance Research Letters vol. 59, 2024)**
- This was a live (forward) experiment using GPT-4 with internet access. Its attractiveness ratings and earnings forecasts correlated positively with later earnings surprises and out-of-sample returns. A strategy built on the ratings earned positive returns. The model changed its view after negative news events. — [IDEAS/RePEc](https://ideas.repec.org/a/eee/finlet/v59y2024ics1544612323011583.html); [Alpha Architect summary](https://alphaarchitect.com/chatgpt/)
- The authors' verdict is "a qualified yes." Accuracy varied over time, so they say LLMs should be treated as assistants rather than the final decision-maker. — [Alpha Architect summary](https://alphaarchitect.com/chatgpt/)

**Multi-agent frameworks**
- TradingAgents (Xiao et al., arXiv 2412.20138, Dec 2024) uses 7 roles: fundamental, sentiment, news and technical analysts, bull and bear researchers, a trader and a risk team. It claims a 26.62% cumulative return on AAPL with Sharpe 8.21 and max drawdown under 1% (19 Jun–19 Nov 2024), and at least 23.21% cumulative return on its 3 sampled stocks. — [arXiv](https://arxiv.org/pdf/2412.20138); [secondary summary](https://blog.pickmytrade.io/build-ai-hedge-fund-tradingagents/)
- A 2026 independent check (blog, not peer-reviewed) found that AAPL buy-and-hold over that window gained about 9.12% ($207.88 to $226.83, adjusted closes). The paper reported a 5.23% loss, so the baseline has the wrong sign. The check also notes that a Sharpe of 8.21 over about 105 trading days points to evaluation artifacts, that costs are not modeled, and that a look-ahead-bias report exists on GitHub (Issue #203). — [DEV Community critique](https://dev.to/trow126/the-most-starred-llm-trading-paper-claims-buy-and-hold-lost-523-it-actually-gained-912-1jj6)

**FINSABER, "Can LLM-based Financial Investing Strategies Outperform the Market in Long Run?" (arXiv 2505.07078, May 2025, revised to v5)**
- It re-tested published LLM timing strategies (FinMem/FinAgent style) over 20 years and 100+ symbols chosen without selection bias, with controls for survivorship, look-ahead and data snooping. The advantages reported in earlier papers "deteriorate significantly under broader cross-section and over a longer-term evaluation." — [arXiv](https://arxiv.org/pdf/2505.07078); [arXiv html v3](https://arxiv.org/html/2505.07078v3)
- LLM strategies are too conservative in bull markets, where they trail passive investing, and too aggressive in bear markets, where they take heavy losses. The authors recommend trend detection and regime-aware risk control over adding framework complexity. — [arXiv](https://arxiv.org/pdf/2505.07078)

### Inferences
- The finding that holds up best is narrow: LLMs read news and filings text better than older NLP tools. The edge is a short-horizon underreaction (overnight to next day), concentrated in small caps and bad news. It is not evidence that LLMs make good portfolio managers.
- The decay series (6.54 to 1.22) is about what textbook alpha decay predicts once the method becomes public and cheap. Anyone starting in 2026 should expect a smaller edge than the 2021–2024 backtests show.
- Agent-framework papers (TradingAgents, FinMem, FinAgent) usually test 3–5 tickers over a few months. That is too little to measure skill, and FINSABER shows their reported advantages do not generalize.

### Gaps
- I did not retrieve exact Sharpe figures for Chen/Kelly/Xiu, by market or for equal- versus value-weighting. The fetched discussant PDF could not be parsed. I also did not confirm whether India is one of the 16 markets.
- I found no 2025–2026 out-of-sample replication of Pelster & Val.
- I did not check whether the Kim/Muhn/Nikolaev paper was re-issued after its "temporary" withdrawal (status as of Feb 2025).
- I did not retrieve primary numbers for FinMem, FinAgent, FinGPT or BloombergGPT. BloombergGPT and FinGPT are known mainly as NLP-benchmark models, not as sources of trading results.

## 2. How serious is look-ahead / memorization bias, and who addresses it?

### Takeaway
For any backtest period inside the model's training window, memorization is severe and cannot be undone by prompting or masking (Lopez-Lira, Tang & Zhu 2025). Post-cutoff tests and point-in-time models (ChronoBERT/ChronoGPT) suggest that the specific news-to-next-day-return signal survives once look-ahead is removed. For entity-level judgments, "distraction" (prior knowledge of the company) can matter more than look-ahead (Glasserman & Lin).

### Cited Findings
- Lopez-Lira, Tang & Zhu, "The Memorization Problem: Can We Trust LLMs' Economic Forecasts?" (arXiv 2504.14765 / SSRN 5217505, 2025):
  - LLMs recall exact pre-cutoff values of macro indicators, news headlines, stock returns and conference-call content.
  - Telling the model to respect a historical date does not stop recall-level accuracy.
  - Masking also fails, because models reconstruct the entity and date from minimal context.
  - After the cutoff there is no recall.
  - They frame this as non-identification: if the model saw the outcome in training, its true forecasting skill cannot be recovered from its outputs. — [arXiv html v2](https://arxiv.org/html/2504.14765v2); [SSRN](https://papers.ssrn.com/abstract=5217505); [Quantpedia summary](https://quantpedia.com/the-memorization-problem-can-we-trust-llms-forecasts/)
- Glasserman & Lin, "Assessing Look-Ahead Bias in Stock Return Predictions Generated by GPT Sentiment Analysis" (arXiv 2309.17322, Sept 2023):
  - They separate two biases: look-ahead (knowing what returns followed) and distraction (general knowledge of the company contaminating the sentiment score).
  - Headlines with company identifiers removed outperformed the original headlines in-sample. Distraction mattered more than look-ahead, especially for large companies.
  - They recommend anonymization even for out-of-sample live use. — [arXiv](https://arxiv.org/abs/2309.17322)
- He, Lv, Manela & Wu, ChronoBERT/ChronoGPT (arXiv 2502.21206, 2025; AEA 2026 program):
  - These are chronologically consistent models trained only on text available at each date.
  - On next-day returns from news, ChronoBERT's real-time outputs reach a Sharpe comparable to state-of-the-art models, so lookahead bias is "modest" for this application.
  - They note bias is model- and application-specific, and that a downstream regression can make up for a weaker language model. — [arXiv html](https://arxiv.org/html/2502.21206v2); [AEA 2026](https://www.aeaweb.org/conference/2026/program/paper/BrRDGQHe)
- "Look-Ahead-Bench" (arXiv 2601.13770, Jan 2026) is a standardized benchmark for measuring look-ahead bias in point-in-time LLMs in realistic financial workflows. — [arXiv](https://arxiv.org/pdf/2601.13770)
- Lopez-Lira & Tang used only post-cutoff data and report that GPT-4 does better later in the sample, which they say is "inconsistent with the model having knowledge outside of its cutoff date." — [arXiv v6](https://arxiv.org/html/2304.07619v6)
- StockBench (Oct 2025) and InvestorBench (Dec 2024) take opposite approaches. StockBench is built "contamination-free" on Mar–Jul 2025 data. InvestorBench used historical data that the LLMs probably saw in training. — [StockBench arXiv](https://arxiv.org/abs/2510.02209v1); [InvestorBench review](https://themoonlight.io/review/investorbench-a-benchmark-for-financial-decision-making-tasks-with-llm-based-agent)

### Inferences
- Rule of thumb: any LLM backtest dated before the model's knowledge cutoff is uninformative about skill. This is especially true for decisions involving named large-cap stocks, which includes NIFTY 50 names.
- Defensible designs are:
  - an evaluation window after the cutoff, or a point-in-time model;
  - anonymized inputs (strip ticker and company name);
  - forward paper trading with logged prompts and outputs.
- The user's existing paper-trading setup is a natural fit for forward testing.

### Gaps
- I found no study measuring memorization on Indian (NSE/BSE) data specifically.
- I did not retrieve detailed Look-Ahead-Bench results.

## 3. Live / forward-tested LLM trading (2024–2026) vs. backtests

### Takeaway
Live and contamination-free results are far weaker than published backtests. In Alpha Arena Season 1 (Oct–Nov 2025, real money, crypto perps), 4 of 6 frontier models lost money. GPT-5 was down about 59–63%. In Season 1.5, 26 of 32 accounts lost money, and the Season 1 winner lost in all four of its runs. In StockBench (2025), most agents failed to beat buy-and-hold. Forward results are noisy single runs with no significance testing.

### Cited Findings
- Alpha Arena Season 1 (Nof1; 18 Oct–3 Nov 2025): each of 6 LLMs got $10,000 of real capital to trade crypto perpetuals on Hyperliquid, with identical prompts and data. — [iWeaver recap](https://www.iweaver.ai/blog/alpha-arena-ai-trading-season-1-results/); [Forklog](https://forklog.com/en/four-out-of-six-ai-models-suffer-losses-in-trading-tournament/)
- Season 1 final P&L: Qwen3 Max +$2,231 (about +22%), DeepSeek +$489 (about +5%), Claude Sonnet 4.5 −$4,201, Gemini 2.5 Pro −$4,555, Grok −$5,792, GPT-5 −$5,874. — [Forklog](https://forklog.com/en/four-out-of-six-ai-models-suffer-losses-in-trading-tournament/)
  - Some media reports put GPT-5's loss at about 63% because they used intermediate snapshots. — [Protos](https://protos.com/llm-crypto-trading-contest-finds-llms-cant-trade-crypto/)
  - Organizers acknowledged that "LLMs do not handle numerical time series data well, but that was the entire context we provided them." — [Forklog](https://forklog.com/en/four-out-of-six-ai-models-suffer-losses-in-trading-tournament/)
- Season 1.5 (US stocks): 8 models × 4 competitions over two weeks gave 32 accounts. Only 6 ended positive, and together they lost 35.2% of $320,000. Season 1 winner Qwen3 Max lost in all four runs (ranks 7, 13, 16 and 31). Nof1's own methodology post disclaims single-run rankings because of low statistical power and run-to-run variance. — Source is a third-party GitHub research PR summarizing Nof1 primary sources, so treat it as secondary: [GitHub PR](https://github.com/josejuarez96/tradepartner/pull/233)
- Alpha Arena's public results ended in Dec 2025, according to a third-party tracker. I found no confirmed Season 2 results. — [TradeRank](https://www.traderank.ai/llm-trading-benchmark)
- TradeRank.ai (a commercial site) runs a live paper-trading benchmark, not real money: 18 models, $10k simulated capital each, crypto plus US equities.
  - As of 3 Oct 2026 the leaders are Nemotron 3.5 Lightning +11.9%, Qwen3.8 Max +8.1% and Inkling (Thinking Machines) +7.4%.
  - Across 9 completed seasons, only 46.2% of model-seasons were profitable, which is roughly coin-flip. — [TradeRank](https://www.traderank.ai/llm-trading-benchmark)
- StockBench (arXiv 2510.02209, Oct 2025) ran multi-month daily buy/sell/hold decisions on Mar–Jul 2025 data, using prices, fundamentals and news.
  - Models tested: GPT-5, Claude-4, Qwen3, Kimi-K2 and GLM-4.5. "Most LLM agents struggle to outperform the simple buy-and-hold baseline," though a few showed better return and risk.
  - Doing well on static finance-knowledge tasks "does not necessarily translate into successful trading strategies." — [arXiv](https://arxiv.org/abs/2510.02209v1)
- InvestorBench (arXiv 2412.18174, Dec 2024) tested 13 LLMs. Proprietary and larger models generally did better, but "all models struggle in highly volatile market conditions." — [InvestorBench review](https://themoonlight.io/review/investorbench-a-benchmark-for-financial-decision-making-tasks-with-llm-based-agent); [alphaXiv](https://www.alphaxiv.org/overview/2412.18174v1)
- Pelster & Val (2024) is the main academic live test of LLM stock selection. It found positive but time-varying value when the LLM is used as a rating assistant. — [Alpha Architect](https://alphaarchitect.com/chatgpt/)

### Inferences
- There is a consistent pattern across sources:
  - LLMs as text scorers that feed a cross-sectional signal work, but the edge is small, decaying and cost-sensitive.
  - LLMs as end-to-end autonomous traders, especially when fed raw price series, have not shown durable live skill.
  - Model rankings flip between seasons, which is consistent with noise rather than skill.
- Two weeks or a few months of live P&L from one run cannot separate skill from luck. The user's own paper books should be judged over many independent trades and regimes, with simple baselines alongside (buy-and-hold NIFTY, plain MACD).

### Gaps
- I found no audited, real-money, multi-year track record of an LLM-driven equity fund.
- I did not verify the Season 1.5 figures from Nof1's own site; they come via a secondary GitHub summary.
- I found no live LLM trading competition on Indian markets.

## 4. Practical cost/latency and best-practice architectures

### Takeaway
The evidence points to a specific design. Use the LLM offline or near-real-time as a feature extractor or sentiment scorer on text (headlines, filings, earnings calls), feeding a conventional quant or rules model with explicit risk control. Do not use the LLM as the trade-by-trade decision-maker on price data. Transaction costs, not inference cost, are what kill the edge.

### Cited Findings
- The embedding-plus-regression approach (Chen/Kelly/Xiu) beats word-based methods and technical signals. It is a feature-extractor design, not an agent. — [Oxford seminar abstract](https://nlpecofin.web.ox.ac.uk/node/3041271)
- ChronoBERT shows a downstream prediction model "can compensate" for a weaker language model. This supports a small, point-in-time encoder plus a quant head. — [arXiv 2502.21206](https://arxiv.org/html/2502.21206v2)
- The tradable LLM news edge disappears at about 20 bps round-trip cost, even for US equities. — [Lopez-Lira & Tang v6](https://arxiv.org/html/2304.07619v6)
- Organizers of autonomous-agent contests concede that LLMs handle numerical time series poorly. — [Forklog](https://forklog.com/en/four-out-of-six-ai-models-suffer-losses-in-trading-tournament/)
- FINSABER recommends regime-aware risk control and trend detection over more complex agent frameworks. — [arXiv 2505.07078](https://arxiv.org/pdf/2505.07078)
- Glasserman & Lin recommend anonymizing company identifiers when scoring. — [arXiv 2309.17322](https://arxiv.org/abs/2309.17322)
- Multi-agent frameworks such as TradingAgents (7+ agent roles, multiple LLM calls per decision) have reported results that were not independently reproducible in at least one key baseline. — [DEV critique](https://dev.to/trow126/the-most-starred-llm-trading-paper-claims-buy-and-hold-lost-523-it-actually-gained-912-1jj6)

### Inferences
- The user's MACD system should not hand trade timing to an LLM. A reasonable add-on design:
  1. Score NSE corporate announcements and news headlines per stock each night or pre-open with an LLM. Strip ticker and company name to reduce distraction.
  2. Store the scores point-in-time with timestamps.
  3. Use them as a veto or filter on MACD entries, for example skipping longs after strongly negative news, or as a sizing tilt.
  4. Paper-test forward against the plain-MACD lanes.
- This also fits the user's own 2 Oct finding that "allocation > filter > exits."
- Latency: a pre-open or 14:45 batch over a few hundred headlines is small. A multi-agent debate per ticker multiplies the number of LLM calls and adds latency. That is not a problem for end-of-day options picks, but it gives no evidence-backed benefit.
- Costs: Indian equity and options round-trip costs (STT, exchange fees, GST, stamp duty, slippage on options spreads) are probably at or above the roughly 20 bps level where the US news-drift edge vanished. A short-horizon LLM news signal on its own may not survive costs. It is more promising as a filter on trades already taken.

### Gaps
- I found no rigorous source comparing per-token or per-decision API cost and latency for trading pipelines, and no source on Indian transaction costs; I did not search either. The cost statements above are inferences.
- I did not retrieve hard numbers on fine-tuned FinGPT or LoRA versus API GPT-class models for sentiment accuracy against cost.

## 5. Applications to Indian markets (NSE/BSE, Hindi/English news, corporate announcements)

### Takeaway
Rigorous evidence is thin. What I found is mostly university or student work applying FinBERT or LLM sentiment to NIFTY 50 or BSE headlines, usually combined with technical indicators. I found no top-journal, post-cutoff, cost-adjusted study of LLM signals on NSE stocks, and no Hindi-language financial LLM trading study.

### Cited Findings
- A comparative study of sentiment frameworks on NIFTY 50 news headlines found FinBERT gave higher accuracy and sensitivity than general models. It reports classification accuracy, not trading returns. — [IJSR 2026](https://www.ijsr.net/archive/v15i6/SR26619135658.pdf)
- "Enhancing Stock Market Forecasting with LLMs, Sentiment Analysis and Technical Indicators" (Christ University) combines LLM sentiment from Google News with technical indicators, using BSE data from Jan 2015 to Jun 2023. That window overlaps LLM training data, so memorization risk applies. — [Christ University archive](https://archives.christuniversity.in/items/show/26160)
- "Stock Market Trend Analysis on Indian Financial News Headlines with NLP" is another student-level study. — [Christ University archive](https://archives.christuniversity.in/items/show/10817)
- Chen/Kelly/Xiu cover 16 international markets in 13 languages, which shows LLM news signals are not US-only. — [Oxford seminar abstract](https://nlpecofin.web.ox.ac.uk/node/3041271)

### Inferences
- The global evidence (Chen/Kelly/Xiu across 16 markets; the small-cap and negative-news concentration in Lopez-Lira & Tang) suggests news underreaction should also exist in Indian mid and small caps, where analyst coverage is thinner. But that is untested here with proper controls.
- The most practical Indian data source is the structured NSE/BSE corporate announcement feed (results, board meetings, pledges, order wins). It is timestamped, which makes point-in-time logging easy.
- For index options (NIFTY, BANKNIFTY), single-stock news sentiment is less directly relevant. Macro and event classification (RBI policy, budget, global overnight news) is more relevant, but I found no evidence on it.

### Gaps
- I found no peer-reviewed study of LLM (GPT-4-class) news or announcement signals on NSE stocks with post-cutoff evaluation and transaction costs.
- I found no Hindi or vernacular financial-news LLM trading study.
- I did not confirm whether India is in the Chen/Kelly/Xiu 16-market sample.
