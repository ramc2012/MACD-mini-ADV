# Independent data and overlay audit

Audit completed 9 October 2026 at 21:41 IST against the final frozen inputs and
analysis outputs identified by SHA256 in `data_audit_results.json`. Analysis
scripts and their outputs were reviewed read-only. Only this audit note and its
receipt were written by the independent reviewer.

## Result

No remaining concrete quantity, accounting, timestamp-ordering, or arithmetic
defect was found in the reviewed version. This validates the stated diagnostic
calculations; it does not validate the trading hypothesis or promote a strategy.

Independent checks covered:

- 220 collected lifecycle paths, each with exactly one BUY; actual SELL quantities
  and retained closed-slice quantities reconcile to the initial BUY quantity.
- 301 retained closing slices match their specific durable exit trade by ID,
  price, quantity, and timestamp. Every selected slice has the same entry basis
  as its single BUY, and administrative exits are absent from overlay candidates.
- 296 optimistic comparison rows, including all 31 triggered arm/position rows,
  were recalculated from the frozen trades and minute bars. There were 3,377
  successful monetary or timestamp comparisons and zero discrepancies.
- The baseline starts with the actual fill price and sums actual SELL proceeds.
  Recorded entry/exit slippage is not charged again. Standardized costs are
  INR20 per order: one BUY plus every retained actual partial SELL plus the new
  overlay SELL, when applicable.
- Partial alternatives retain actual sales that occurred by the modeled overlay
  fill time; residual quantity equals initial BUY quantity minus those sales,
  remains a positive whole number of exchange lots, and replaces all later
  actual sales. Full-position and partial-position cash flows reconcile.
- Trigger calculation uses only completed minute closes after the entry partial
  minute. Its running peak contains no future closes or highs. Fill time is the
  next eligible minute open and does not exceed the actual final exit timestamp.
  Overnight, weekend, and 2 October holiday gaps follow the frozen derivative
  session schedule, 09:15 through 15:39 minute openings.
- Additional 20bps exit stress is applied equally to actual and modeled sale
  proceeds; standardized fees retain the correct order counts.
- Primary results use the same 145 positions for both activation arms. The three
  positions with a zero-volume hypothetical fill minute under either arm are
  excluded from both arms. The 148-position optimistic sensitivity remains
  separately inspectable.
- The summary input SHA matches `evidence.json`; both aggregate arms reconcile to
  their row-level CSV results. The MP API error is reported as unavailable,
  rather than as a confirmed empty book.

## Corrections made by the analysis author after audit feedback

1. Extended the derivative schedule from 15:30 to the app's 15:40 close, preventing
   omitted minutes and incorrect next-session fills. The root reviewer separately
   verified NSE timing against its official market-timings page.
2. Explicitly excluded administrative expired-contract valuations from overlay
   candidates.
3. Distinguished the MP endpoint's HTTP404 from zero holdings.
4. Separated zero-volume modeled fills into an optimistic sensitivity. The first
   two such fills contributed INR101,678.78 to the initial single-close +50% arm;
   the partial-aware extension found a third. The final common primary cohort
   excludes all three positions.
5. Included constant-basis single-BUY/multiple-SELL lifecycles, preserving actual
   partial proceeds and the changing residual whole-lot quantity.

## Supported interpretation

The final primary +50% activation arm changes 19 of 145 closed lifecycles:
14 improve and five worsen. Its modeled net improvement is INR327,752.08;
incremental 20bps stress leaves INR327,096.70. The +100% activation arm changes
eight positions, all improving in this retained sample, for INR282,615.71;
stress leaves INR282,050.48. Both full primary baselines and overlay totals remain
negative. These are differences for the eligible retained cohort, not portfolio
returns or a forecast of future profit.

The main MACD lane contributes only INR34,465.04 of the +50% pooled difference
(seven changed lifecycles: five improve, two worsen), and INR11,911.55 at +100%
(two changed). Pooled gains primarily come from Blast and one Smart50L result;
different lane capitals cannot be treated as independent equal-sized replications.
The top three favorable differences account for 68.81% of the +50% net difference
and 65.70% of the +100% net difference. A small number of outcomes carries much
of the result.

The following limits remain material:

- Data-quality eligibility uses complete paths through the actual exit and full
  future minute volume at a hypothetical fill. It is a retrospective paired
  cohort filter, not a live executable selection rule. Positive full-minute
  volume does not establish that a bid or sufficient depth was available at the
  minute open.
- Exact-contract bid/ask, order-book depth, market impact, action latency, full
  statutory charges, and reliable historical feed-mode tags are absent.
- Open positions and pyramid/add lifecycles are omitted. Completed-lifecycle
  selection can overrepresent losers or underrepresent still-running winners.
  The large open Blast winners are therefore outside this closed-cohort test.
- Early exits can change later entries, available cash, concurrent exposure, and
  capacity. This isolated exit diagnostic does not model those portfolio effects
  or equal starting cash/inventory across alternative full strategies.
- Both tested arms and all retained historical data have been inspected. There
  is no untouched holdout, no multiple-testing-adjusted claim, and no prospective
  evidence yet. The underlying-confirmed partial-exit hypothesis in POLICY.md is
  a different, untested policy.

Relevant reviewed code: `data_analysis/collect.py:63` through 82 (eligibility and
paths); `data_analysis/analyze.py:26` through 34 (session schedule), `:77` through
104 (single close), `:106` through 152 (partial cash flows), `:154` through 166
(API availability), and `:174` through 195 (paired quality filter and output).
