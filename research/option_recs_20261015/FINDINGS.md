# External review of the option logic: what the data says (15 Oct 2026)

The user pasted an external review with five upgrade areas: Greek/DTE-aware strike selection with an IV-vs-RV gate;
a blast breakeven ratchet, time stop and volatility-scaled trail; a bid-ask spread gate and order-flow alignment;
Market Profile / OI-wall gating; and portfolio Greek limits. Each was checked against the code, then tested on data.
Analysis only: no engine code or setting was changed, and every database was opened read-only.

## Bottom line

| Recommendation | Premise vs code | Evidence | Verdict |
|---|---|---|---|
| Blast breakeven ratchet at +22% | **Partly wrong.** The trail arms at **+30%** (not +40%) and is floored at round-trip breakeven, so "+38% then round-trips to −50%" cannot happen. Only trades peaking between +22% and +30% are affected. | Paired on the same entries: −3.5 pp/trade on the replay (t −1.7), −4 to −6 pp on live-band replay entries, −1.0 to +0.6 pp on the live journal, +1.5 pp on its 68 screen passes (t 0.6). | **Not supported.** The sign flips between samples and no |t| reaches 2. Keep the current rule. |
| 8-bar time stop if never +10% | Fair (no time stop exists) | −6.3 pp on the replay, about 0 on live-band entries, +0.6/+1.6 pp on the journal, +0.2/+1.4 pp on screen passes. | **Not proven.** Small and inconsistent. |
| Volatility-scaled trail | Fair | +0.9 pp on the replay (t 1.0–1.4), −3 to −5 pp on live-band entries. | **Not proven.** |
| (control) tighter −35% stop | — | −3.4 / −4.7 pp on screen passes, −0.9 to −3.9 pp elsewhere. | Confirms the 2 Oct finding: tight exits lose. |
| Delta band 0.25–0.40 for blast | **Wrong premise.** 0-DTE contracts are excluded (min DTE 1). Taken entries run median \|Δ\| 0.24 at 15+ DTE and 0.38–0.45 at 1–7 DTE: not "deep OTM lottery tickets four days out" and not "ITM on expiry day". | The proposed band was the *worst* middle band: 0.25–0.40 averaged −5.5% vs +1.2% for 0.15–0.25. Only \|Δ\| > 0.60 (n 14, 3 days) was clearly bad (−27%, 57% hit −50%). | **Not supported.** A \|Δ\| ≤ 0.60 cap is worth shadow-logging. |
| IV_TOO_EXPENSIVE (IV ≫ RV) | Fair. At entry, median IV 23.8% and RV20 23.6%, so options are not systematically rich. | IV/RV > 1.3 rows hit −50% far more often (48–61% vs 5–11%) but doubled as often (17–20%). Return difference: opposite signs across fill models. | **Not proven.** The payoff gets fatter-tailed, not worse on average. Log it. |
| SPREAD_TOO_WIDE gate | Fair | Spread > 2% of mid: doubling rate 4–11% vs 17–26%. Returns are worse but noisy (day-paired t ≈ 0). | **Weak signal.** The bigger issue is **paper fills ignore the spread** (below). |
| Underlying CVD alignment | Fair (flow lives in the tick store only) | Stocks on condensed days, 201 rows, **4 days**: flow against the side −11.4% vs with it −4.3% (day-paired +6 to +8 pp, t 1.0–1.6). Doubling rates go the other way. | **Not proven.** The most promising filter; worth a shadow log. |
| Value-Area middle-half block | Fair | Middle-half rows did *better* (−2.1%/+7.2%) than the rest (−4.3%). | **Not supported.** |
| OI-wall block | Fair | **Untestable on our data:** condensation drops per-minute OI, and only 3 raw-tick days keep it. The prior TradeBot NIFTY study (Feb–Mar 2026) found walls break 60% of the time within 72 bars, and its out-of-sample probe scored corr 0.066. | **Not supported.** Keep OI in condensed rows to test later. |
| Portfolio Greek limits | Fair. Today's caps are gross premium only. | **Real.** At the 14 Oct close, the MACD book holds 36 CE / 7 PE: net Δ **+₹5.47 Cr of underlying, 5.1× its ₹1.07 Cr equity**. A 2% fall costs about ₹11 L through Δ alone, and theta is −₹99k/day (2.3% of premium). Blast holds net Δ −₹76 L (gross ₹3.7 Cr) on ₹50 Cr. | **Real visibility gap.** Show net Δ / θ / vega per book. Limits only after the user chooses them. |

### Two problems found that the review did not name

1. **Paper fills do not pay the spread.** `execution.py` fills at last price ± `slippage_bps` (5 bps, so 0.1%
   round trip). At entry, stock options in the journal had a median spread of **1.92%** of mid (index options 0.25%;
   quartiles across all rows 0.86–2.43%). Crossing it costs about half the spread each way, so every stock-option
   round trip is booked about **1.8 pp** better than a real fill. That applies to all lanes, and more often to
   the MACD lane, whose ladder trades in more legs. Fix: buy at the ask and sell at the bid when a fresh two-sided
   quote exists, falling back to bps otherwise.
2. **Replaying the band from stored minute bars is biased toward winners (survivorship).** The 90-day background
   fill gives a contract history from before it entered the tradable band. Contracts that later moved into the band
   are the ones whose underlying moved toward them. On the 10 sessions with tick coverage, **77%** of the replay's
   premium-cleared crosses (1,266 of 1,649) were contracts that never ticked live that day. Replay passes averaged
   +31.6% per trade (37% peaked above +100%). The live lane averaged **−3.7%** on 42 closed trades.
   `downloaded_at` cannot tell live bars from backfill (re-downloads overwrite it). Only the tick store's
   per-day membership can. **Any future replay must restrict to contracts that ticked live that day.** The
   blast lane's own Jun–Sep walk-forward should be checked for this before its numbers are relied on.

## Data and method

- **Replay** (`extract.py`, `screen.py`, `paths.py`, `exits.py`):
  - Option minute bars from 20 Aug to 14 Oct 2026, rolled into the lane's 09:15-aligned 30-minute signal bars.
  - MACD 12/26/9 seeded at the first close, as `indicators.py` does.
  - Legacy screen: premium/spot ≤ 1.2%, breadth ≥ 0.50 over ≥ 20 contracts, ≥ 25% below the 750-minute high,
    ≥ 120 minutes of history, DTE ≥ 1.
  - Result: 26,604 crosses and 736 passes; 656 entries after one position per contract.
  - Approximations: the recent high is taken from 30-minute highs, and breadth from 30-minute MACD. The VIX
    allowance and the liquidity cap are not replayed.
  - Data fix: 3,137 contracts are stored with both a set and a blank expiry. extract.py read them twice, and
    `screen.py` dedupes them.
- **Live journal** (`journal.py`, `analyze_journal.py`; report in `out/journal_report.txt`):
  - Rows: 408 blast journal rows from 29 Sep to 14 Oct that cleared the premium gate (51 TAKEN, 17 ENTRY_REJECTED,
    328 BREADTH_TOO_THIN, 12 NOT_OFF_HIGH). 407 have paths.
  - Outcomes replay each exit on 1-minute bars from the journal time to expiry-day 15:10, using two fill models:
    - optimistic: peak from highs, fills at the stop level;
    - conservative: peak from closes, fills at the worse of the level and the close.
  - Simulator check: on 36 contracts the lane really traded, live averaged −5.2% against −0.7% (optimistic) and
    +2.0% (conservative), with correlation 0.70. Absolute levels are flattering by 5–7 pp, but paired differences
    are informative.
  - Greeks: Black-Scholes at r 6.5% on the journal's own premium and spot. RV20 is close-to-close up to the
    previous session. Spread and flow come from the tick store. The value area is the previous session's, using
    70% of volume for stocks and of time for indices.
- **Live-band check** (`ticks_extract.py`, `members.py`): band membership is taken from tick-store rows per day.
- **Portfolio Greeks** (`portfolio_greeks.py`): open MACD and blast positions at the 14 Oct close (52 positions).

## Power

The journal spans 9–10 sessions and the screen passes only 68 rows. Every verdict above is "not proven" or "not
supported" in the sense of [[judging-option-strategies]]: nothing here rejects a rule outright. The two kinds of
question have very different power:

- **Exit rules are paired on the same trades.** The paired difference has SD 10–13%, so about 100–160 independent
  trades detect 2 pp at t 2. The journal's 407 rows (clustered in 9 days) showed no variant beyond ±2 pp, and the
  sign flipped between samples. That is reasonable evidence that the proposed exits do not add a large edge.
- **Entry filters compare different trades.** Per-trade SD is 40%, so a 2 pp difference needs about 3,000+ trades.
  Those verdicts are genuinely underpowered.

Shadow-logging the candidate features (Δ, IV/RV, spread, signed flow) in `blast_journal` costs nothing and builds
that sample.
