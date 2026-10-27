# Vanguard and CBE data views and paper execution

Prepared and verified on 2026-10-27. The owner requested visible market data,
explanations behind note icons, a compact fresh-ledger selector, a reset CBE cash
lane, and Vanguard execution in ATM options rather than a watchlist-only product.

## Result

- The fresh Vanguard Market tab and the CBE Market tab show the current active
  F&O stock universe, selectable historical sessions, daily price charts and
  indicator/MP/flow/positioning/IV/futures/chain/delivery lenses. They do not depend
  on the missing retained `candidate_evaluations` journal.
- At the latest completed source session, ending **2026-10-26 15:30:00 IST**:
  213/213 names have a completed daily observation and a current-session MP row;
  209 have enough history for daily indicators. ANANDRATHI, ENRIN, ICICILIFE and
  UJJIVANSFB remain explicitly unavailable for those indicators. The four names
  stay visible. The historical session ending **2026-10-23 15:30:00 IST** shows
  212 current daily/MP observations; a stale price is dated as such.
- This is a read-only reconstruction of indicators from available local prices,
  not reconstructed original decisions, trades or historical availability.
  The universe is today's active catalog, not a point-in-time historical universe.
- The large scope/model/feature descriptions moved behind information buttons.
  The compact ledger selector defaults to the fresh lane. Qwen's popup filters
  recent reviews to the lane being viewed. The original MACD lane is unchanged.

## CBE daily cash paper book

The owner-authorized reset completed at **2026-10-27 08:09:14 IST**. The new book
starts with INR 5,000,000, no orders, positions, fills or decisions. The prior
epoch had two pending orders, no positions/fills and five events. Both a portable
SQLite backup and the original database are retained under:

`runtime/paper_lanes/archives/cbe-20261027-reset/`

The manifest links old/new account IDs. Publication reconciliation checks the
account ID, so old pending cohorts cannot enter the new book. The fresh UI does
not reuse the old epoch's watchlist. The original Nomad history stays separate.

CBE evaluates all 213 active F&O stocks as cash-equity underlyings, long only,
using complete daily OHLCV, EMA20/50, 5/20-session momentum, relative momentum,
MACD12/26/9, RSI14 and MP. Its deterministic trend qualification remains explicit;
Qwen receives these indicators and the broader dated shared context to accept,
reject or wait. It does not change quantity, stops or risk limits.

The data reader prefers the existing NSE daily report when available and falls
back to a complete local broker session: twelve 30-minute bars plus the final
15-minute closing bar. Recovery-tagged Upstox bars are accepted only on the same
aligned grid with valid OHLC. No partial close, missing price or gap is filled.
The original October recovery rows restore the missing benchmark session ending
**2026-10-08 15:30:00 IST** without a download or database rewrite.

Qualification requires at least 60 observed common daily closes, every one of
the last 50 exchange sessions in both stock and benchmark, and MP from the same
completed source session. Older seed gaps are disclosed to Qwen. Twelve names
qualify deterministically in the latest local snapshot; the top ten form the
bounded review pool. This is not an approval, profitability claim or a fill.

CBE publishes after the completed cash session and current MP refresh, within
**2026-10-27 15:31:00 IST–2026-10-27 16:30:00 IST** for this session. Its first new
book's possible entries are the next exchange session, within
**2026-10-28 09:20:00 IST–2026-10-28 09:55:00 IST**, subject to a fresh Qwen review,
fresh executable quotes, depth, cash and risk limits. Stops/targets and due exits
run separately; sale proceeds settle at the next session open. Existing 10%
position, 100% gross, 30% sector, ten-position, 5% stop and 10% target limits are
retained. No historical fill is manufactured to start the account.

## Vanguard ATM options execution

The fresh lane uses its inherited direction and option-payoff rankers and the
existing flow/regime/sector/timing breadth gates. It restricts the execution pool
to ATM contracts, one side/horizon per stock and up to ten ranked candidates.
Retained research may still compare wings and both sides.

The same-session candidate window for this session is
**2026-10-27 14:45:00 IST–2026-10-27 15:15:00 IST**. A fresh candidate enters the
paper queue immediately, not the next morning. The Qwen review happens against
the fresh executable quote; there is no duplicate planning inference consuming
the entry window. Qwen also receives the full shared context and the new daily
indicator group, with units, timestamps, missingness and seed gaps.

Before review and again before a fill, the selected strike must still be nearest
to the fresh underlying bid/ask midpoint in the actual expiry's catalog. If it
has moved away from ATM, the order waits/skips with a reason; no different
contract silently inherits its approval. Whole-lot metadata, option depth,
spread, available premium cash and existing exposure gates still apply.

The funded paper ledger records orders, quantities, adverse-slippage L1 fills,
estimated costs, stops, targets, holding deadlines, equity and P&L. Exits never
wait for Qwen. Existing 5% position, 40% gross, 20% sector, ten-position, 15% stop
and 30% target limits remain. The hold is one to three sessions. Expiry must
survive the selected hold. Real orders are impossible in this service.

The closed-session admission diagnostic for the entry bar ending
**2026-10-26 14:45:00 IST** found 213 spot observations but zero symbols passing
the combined fresh-feature check: the available same-day regime snapshot is
from **2026-10-26 15:15:00 IST**, after the entry bar, while the earlier joined
regimes are stale. This diagnostic creates no publication or order. The later
snapshot is not backdated, and the 80% breadth gate remains. The existing
completed-bar feature worker must supply current-session regime/timing inputs
for the next real entry evaluation. This is an armed paper engine, not evidence
that this historical admission check passed or that a live-session fill occurred.

## Validation and preservation

- 117 isolated Python tests and 88 frontend tests pass; TypeScript/Vite builds.
  Python tests run without networking and use temporary books, context and state.
- Tests cover full-universe visibility, recent gaps/stale MP, old seed disclosure,
  daily RSI/MACD, authenticated bounded views, source time versus observation
  time, reset/archive integrity and account epoch isolation, ATM-only selection,
  same-session option admission, an ATM move blocking a fill, then a simulated
  approved entry and a managed target exit without Qwen.
- Browser-checked populated Vanguard tables, historical session selection, price
  chart, information dialog and the fresh CBE INR 5,000,000 account.
- The original seven MACD books' execution-table counts/hashes and the saved
  settings hash match before/after. Vanguard's account ID and INR 5,000,000
  balance remain intact. Original Nomad books/workers were not changed.
- Only frontend and shadow-lanes were restarted. No engine, broker websocket or
  platform feed restart was necessary. Runtime source mounts and served asset
  hashes were checked. Images were built offline from the existing image;
  rollback frontend: `macd-mini-parallel-frontend:before-lane-data-20261027`.
- Evidence: `runtime/lane-data-20261027/` (tests, reset receipt, runtime snapshots,
  protected-state fingerprints, served parity, deployment logs and screenshots).

The serving model is the original local `qwen3.5:9b`; no adapter was promoted or
new training run performed. The richer prompt is not proof of improved edge.
This was verified before market open: no actual new Vanguard/CBE entry or exit
has yet been observed for this deployment. In-session latency and feed/quote
freshness remain conditions for the first paper cycle. Historical broker
downloads remain disabled; this change made none.
