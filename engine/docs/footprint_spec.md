# Professional order flow / footprint — specification

Status: design spec, `spec_version: 1`. Two build agents consume this: one
server-side (`footprint.py`, `orderflow.py`, `app.py`), one client-side
(`FootprintChart.tsx`, `OrderFlowWorkspace.tsx`). Field names and semantics
below are normative — build to them exactly, because the other agent is
building the other half against the same names.

---

## 0. The constraint this whole document is written under

NSE publishes trade price, quantity, time and the best bid/ask. It **never**
publishes who was the aggressor. Every bid/ask split, delta, imbalance, CVD,
absorption and divergence in this system is therefore an **inference**
(Lee-Ready: quote rule, then mid rule, then tick test, with zero-tick carry).
The owner's own measurement: reconstructing a session without quotes
reclassified 20.7% of trades and 14.1% of volume against the live quote-aware
run.

That splits the marker set cleanly in two, and this split is the spine of the
design:

| basis | markers | trustworthiness |
|---|---|---|
| **`volume`** — computed from `bid + ask + unclassified` per row, i.e. total volume at price, which NSE publishes exactly | per-bar POC, value area (VAH/VAL), low-volume nodes, single prints, volume heat, bar volume | **exact.** No inference. Carries no caveat. |
| **`inferred`** — depends on the aggressor split | delta, CVD, single-row imbalance, stacked imbalance, unfinished auction, absorption, exhaustion, delta divergence | **estimate with a confidence.** Never render as a measurement. |

A professional surface makes that boundary visible. Today's does not: the
gold POC (exact) and the green imbalance fill (inferred) render with the same
visual authority. Fixing that is not decoration — it is the difference between
a desk that sizes off measurements and one that sizes off guesses.

---

## 1. Prerequisite: the price row grid

**Nothing else in this spec is buildable until this is fixed.** Stacked
imbalance, unfinished auction, value area, LVNs and single prints all need
*contiguous, populated rows*. Today there are none.

### 1.1 Measured evidence

`GET /api/mp/footprint/BSE:SENSEX26SEPFUT?bars=8&timeframe_seconds=300`
(2026-08-27, market closed, live container):

```
tick_size            0.05
bars                 2
visible price span   77800.00 .. 77897.15
=> rowCount          1944          (FootprintChart.tsx:174)
populated levels     39 across both bars
imb flags fired      0 of 39
```

1,944 grid rows for 39 populated ones. In a ~450px plot that is
`rowH ≈ 0.23px`, so `cellH` clamps to 1 (line 268), `textMode` is false
(line 259), and the axis labels every 113th row — one label per 5.65 points.
The chart draws twenty hairlines in an empty 1,944-row lattice. And because
adjacent rows are essentially never both populated, the diagonal neighbour
lookup finds nothing and **imbalance is structurally dead**, on every
instrument whose range is wide relative to its tick.

No professional platform draws a footprint on the raw exchange tick. Sierra
Chart, ATAS and Jigsaw all expose *ticks-per-row*. This codebase must too.

### 1.2 Rule

Introduce a **row size** distinct from the tick size:

```
row_size = tick_size * row_ticks
k        = round(price / row_size)          # integer row index — the key
row_price= k * row_size                     # only for display
```

* **Key `FootprintBar.levels` by the integer `k`, not by a float price.**
  This is the fix for a whole class of bug at once: neighbour lookup becomes
  `levels[k-1]`, exact by construction, replacing
  `self.levels.get(round(price - tick_size, 2))` (`footprint.py:92`), whose
  hard-coded 2 decimal places is wrong for any sub-paisa tick and whose float
  arithmetic is exactly the failure mode this codebase already documents in
  `orderflow.absorption` ("100.20 − 100.00 = 0.20000000000000284").
* One grid for the whole payload, snapped to an absolute origin (`k` is
  absolute, not relative to the bar), so rows align across bars **and across
  polls**. A grid that re-origins per bar makes stacks and shelves meaningless.

### 1.3 Choosing `row_ticks`

Both a manual override and an auto default:

* `payload` accepts `row_ticks: int | None`. `None` = auto.
* Auto rule: over the returned window, take the **median bar range in ticks**;
  pick the smallest `row_ticks` from the ladder `1, 2, 5, 10, 20, 50, 100,
  200, 500, 1000` such that `median_range_ticks / row_ticks <= 30`. Target ~20–30
  rows for a typical bar; that is the readable range.
* **Stability matters more than optimality.** Recompute only when the median
  range moves by more than 2× from the value the current `row_ticks` was
  chosen for; otherwise the grid flickers between 3-second polls and every
  stack/shelf jumps. Publish the chosen value so the client can render it and
  so the choice is reproducible.
* UI: a `rows: auto | 1 | 2 | 5 | 10` control in `OrderFlowWorkspace`'s toolbar
  beside the timeframe buttons.

### 1.4 Tick size is per instrument, and currently triplicated

`FootprintBook.__init__(tick_size=0.05)`, `OrderFlowTracker.__init__(
tick_size=0.05)` and `market_profile.Profile.tick_size = 0.05` each hard-code
the same constant independently, and nothing in the codebase resolves a tick
size per instrument (`grep tick_size src/macd_trader/*.py` finds only those
three). Consequences measured live:

* Diagonal imbalance cannot fire on a **0.10-tick instrument** at all: no
  trade can occur at `P − 0.05`, so `below` is always `None` and the buy
  branch is dead. (This is the audit's finding; §1.1 shows it is not the only
  way imbalance dies.)
* `flow.absorption.span_ticks` on SENSEX futures came back **1943.0** against
  a gate of `ABSORPTION_MAX_SPAN_TICKS <= 4.0`. The gate is unpassable. The
  constant is right; the denominator is wrong.

**Required:** one owner — a `tick_size_for(symbol) -> float` resolver (segment
+ instrument-class table, defaulting to 0.05) consumed by all three modules.
`FootprintBook` keeps `self.tick_sizes: dict[str, float]` populated at
`watch()`; `OrderFlowTracker` keeps the same per symbol and uses it in
`bucket()` and in the absorption span gate.

### 1.5 OHLC must stay on raw prices

`on_print` currently calls `bar.add(self.bucket(price), ...)`, so `o/h/l/c`
are grid prices. Harmless at 0.05-on-0.05; after row aggregation a 5-tick row
quantises the bar high by up to 5 ticks, and the unfinished-auction test would
then key off the wrong extreme. **Keep raw price for `o/h/l/c`; use `k` only
for the ladder.**

---

## 2. Volume conservation in the bar (blocking defect)

`FootprintBar.add` adds `size` to `self.volume` unconditionally but writes to
`self.levels` only when `side != 0`. Measured live:

```
bar 1787824500:  v = 1980   Σ(bid+ask) over its 20 levels = 1900   gap = 80
tape for that bar: exactly one print with side 0, size 80
```

An 80-lot hole in the ladder, with **no field on the bar or the level from
which a reader could recover it**. This is the same defect that was fixed in
`FlowState` on 2026-08-27 (`total_volume` / `unclassified_volume`) and it was
not carried into `FootprintBar`. It also silently under-counts the value area,
which must be computed on total volume at a row.

**Required:**

```python
FootprintBar.unclassified: float = 0.0
FootprintBar.levels[k] = [bid_vol, ask_vol, unclassified_vol]   # 3-wide
```

* `add()` routes `side == 0` size into slot 2 and into `self.volume`.
* `merge()` sums slot 2.
* `payload()` publishes `"u"` per level and `"u"` per bar.
* Invariant, and a test asserting it: **`Σ(bid + ask + u) == v` for every bar.**
* Row volume for POC / VA / LVN is `bid + ask + u` — total volume at price,
  the exact quantity.

Related, and to be asserted by a test rather than debugged now: the live
payload reports `flow.unclassified: 4` prints alongside
`flow.unclassified_volume: 0.0` and therefore `classified_share: 1.0` —
"100% classified" for a symbol whose own bar has 80 sideless lots. (`flow.
methods` has no `zero_tick` key, so the running container predates the
2026-08-27 fix; the integrator has not redeployed.) Whatever the cause, add:
**`unclassified > 0 ⟹ unclassified_volume > 0`**, and never print a
confidence percentage the visible ladder contradicts.

---

## 3. The marker set

For each marker: (i) meaning, (ii) computation, (iii) published shape,
(iv) render, (v) reliability given the aggressor is inferred.

All markers are computed **server-side** in `footprint.py` and published. The
client renders; it does not re-derive. One exception is noted in §3.7.

### 3.1 Single-row diagonal imbalance (already present — repair it)

**(i)** Aggressors at price P overwhelmed the resting liquidity one row away.
The diagonal comparison (ask at P vs bid at P−1) is correct and already
implemented; three things about it are not.

**(ii)** Per row `k`, with `L[k] = [bid, ask, u]`:

```
buy_ratio  = L[k].ask / L[k-1].bid          # buyers paying up
sell_ratio = L[k].bid / L[k+1].ask          # sellers hitting down
imb_buy  = L[k].ask  >= imb_floor and buy_ratio  >= IMBALANCE_RATIO
imb_sell = L[k].bid  >= imb_floor and sell_ratio >= IMBALANCE_RATIO
```

Three repairs, all required:

* **A zero or absent neighbour is the strongest imbalance, not "no
  imbalance".** The current guards `below[0] > 0` and `above[1] > 0`
  (`footprint.py:96,99`) skip exactly the extreme case — heavy ask above a row
  with *no* bid volume is an infinite ratio, and every platform flags it. A
  missing row (`below is None`) is the same case. Treat an absent/zero
  neighbour as satisfying the ratio **provided the numerator clears
  `imb_floor`**, and publish `"imb_ratio": null` for it rather than a
  fabricated infinity.
* **The two diagonals are independent tests of independent cells.** The
  current `elif` (`footprint.py:99`) means a row that satisfies both reports
  only `"buy"`. Publish both.
* **`MIN_IMBALANCE_VOLUME = 20` is an absolute lot count applied to every
  instrument.** Twenty units is sub-lot on a NIFTY future and a meaningful
  print on a thin weekly option. Scale it:
  `imb_floor = max(MIN_IMBALANCE_VOLUME, IMBALANCE_VOLUME_FRACTION * median_row_volume(bar))`
  with `IMBALANCE_VOLUME_FRACTION = 0.25`. Publish `bar["imb_floor"]` so the
  flags are reproducible from the payload alone.

**(iii)**
```jsonc
level: { "k": 1557439, "p": 77872.00, "bid": 40, "ask": 180, "u": 0, "d": 140,
         "imb": "buy",            // dominant (higher ratio); tie -> "buy". Kept for old clients.
         "imb_buy": true, "imb_sell": false,
         "imb_ratio": 4.5,        // ratio of the dominant side; null when the neighbour is empty
         "poc": false, "va": true, "lvn": false }
bar:   { ..., "imb_floor": 25 }
```

**(iv)** Unchanged from today and already correct: a `buy` flag tints the ASK
half, a `sell` flag the BID half (`FootprintChart.tsx:282-288`). When both
fire, tint both halves.

**(v)** **Weak-to-moderate.** One inference per cell, on a ratio, and the
ratio's denominator is a *neighbouring* row whose classification errors are
uncorrelated with the numerator's. Usable as texture; not as a standalone
trigger. Desaturate at confidence grade `low` (§3.8) rather than hiding — the
bid/ask numbers stay readable, the colour stops asserting.

### 3.2 Stacked imbalance — the institutional footprint marker (absent)

**(i)** N or more *consecutive* rows imbalanced in the same direction. It says
aggressors ran through several prices without resting size stopping them. The
far edge of the stack is the level traders mark and defend — it is the single
most-used footprint marker on a professional desk, and this codebase has no
equivalent.

**(ii)** After per-row flags:

```
Walk rows in ascending k. A run is a maximal set of consecutive integer
indices k, k+1, ..., k+m-1 where every row has imb_buy (resp. imb_sell).
- "Consecutive" means indices differ by exactly 1.
- A missing row (no volume at that k) BREAKS the run. Do not skip it, and do
  not treat it as imbalanced.
- Keep runs with m >= STACKED_IMBALANCE_MIN (default 3, the ATAS/Sierra
  convention).
extreme = top row of a buy stack, bottom row of a sell stack.
```

New constants in `footprint.py`: `STACKED_IMBALANCE_MIN = 3`.

**(iii)** Publish the run, not merely the per-row flags — a zone you cannot
address is not tradeable:

```jsonc
bar["stacks"] = [
  { "side": "buy", "k_from": 1557437, "k_to": 1557440, "rows": 4,
    "from": 77871.85, "to": 77872.00, "extreme": 77872.00,
    "volume": 1820, "suppressed": false }
]
```

**(iv)** A vertical bracket/ribbon spanning the stacked rows on the aggressed
side of the divider, at higher opacity than a single-row imbalance (0.34 vs
today's 0.22) so a stack reads as *different in kind*, not merely as more of
the same. Plus a horizontal dashed shelf projected **rightwards** from
`extreme` across subsequent bars, terminating at the first bar that trades
through it. The shelf is the point; the ribbon alone is decoration.

**(v)** **Weakest marker in the set, and the most seductive.** It is a
conjunction of N ratio tests over ~2N inferred cells; one misclassified print
can break a real run or fabricate a false one, and the false-positive rate
rises superlinearly with per-print error. It compounds classifier error rather
than averaging it out. Therefore:

* **Suppress at grade `low`**: emit with `"suppressed": true` and a reason
  string (API stays honest), render nothing.
* **Never emit a stack whose rows are entirely `zero_tick`-classified** — that
  is a run built from carried-forward guesses.
* Require `classified_share >= 0.85` and `quote_share >= 0.50` for the symbol.

### 3.3 Unfinished auction / unfinished business (absent)

**(i)** The auction *finishes* at a high when the topmost row shows buyers
stopped paying up — the top row has **zero ask volume** (the last trade there
was into the bid). It finishes at a low when the bottom row has **zero bid
volume**. If both sides traded at the extreme, the auction is **unfinished**
and the market is expected to return to that price to complete it. Dalton's
"unfinished business"; drawn in Sierra Chart as a short nose at the bar
extreme.

**(ii)** With `top` = level at the bar's maximum populated `k`, `bot` = minimum:

```
unfinished_high := top.ask > 0 and top.bid > 0
unfinished_low  := bot.bid > 0 and bot.ask > 0
```

Because the aggressor is inferred, one stray print at the extreme flips a
boolean. So make it **three-state, not boolean**, with a floor:

```
floor = max(1, UNFINISHED_MIN_FRACTION * bar.v)      # UNFINISHED_MIN_FRACTION = 0.02
high := "unfinished"  if unfinished_high and min(top.bid, top.ask) >= floor
        "weak"        if unfinished_high
        "finished"    otherwise
```

Guards — publish `null`, never a default:
* fewer than 2 populated rows in the bar → `null`
* the extreme row's volume is entirely unclassified (`bid == ask == 0`,
  `u > 0`) → `null`, not `"finished"`. "Finished" is a claim.

**(iii)**
```jsonc
bar["unfinished"] = { "high": "unfinished" | "weak" | "finished" | null,
                      "low":  "unfinished" | "weak" | "finished" | null,
                      "high_price": 77897.15, "low_price": 77800.00 }
```
`high_price` / `low_price` are the **raw** bar extremes (§1.5), which is why
raw OHLC must survive row aggregation.

**(iv)** A short horizontal nose drawn outward from the extreme row on the
price-axis side: sell-coloured at an unfinished high, buy-coloured at an
unfinished low. `"weak"` at half opacity. Project it rightwards until traded
through, using the same shelf mechanic as stacks — an unfinished high three
bars back is a live target, and a nose that vanishes when the bar scrolls is
useless.

**(v)** **Moderate-to-good — the most robust of the inferred markers.** It is a
*zero test*, not a ratio test: it asks only whether the minority side has any
volume at all at one row. It is immune to misclassification of interior
prints. Its exposure is a single misclassified print at the extreme — and
extremes are precisely where the tick rule is weakest (price moving fastest,
quotes stalest). Hence the floor and the `"weak"` state; do not collapse them
back to a boolean.

### 3.4 Per-bar value area + POC (POC present, VA absent)

**(i)** The rows holding 70% of the bar's volume around its POC — the bar's
own *accepted* range. A bar whose VA sits entirely above the previous bar's VA
is a genuine migration of value, which is the auction-theory read this desk
already trades at session scale (`market_profile.py`) and cannot currently
make at bar scale.

**(ii)** Reuse the algorithm already in `Profile.value_area` — alternating
expansion from the POC — over **volume per row** rather than TPO counts:

```
counts[k] = bid[k] + ask[k] + u[k]            # total volume at price. EXACT.
target    = sum(counts) * VALUE_AREA_FRACTION # import the constant from
                                              # market_profile; do NOT redefine it
start at the POC row; repeatedly take whichever neighbour (above / below) has
more volume, tie -> above (matches `if above >= below` in market_profile.py:132);
stop when included >= target.
```

Two deviations that must be explicit, not silent:

* **POC tie-break.** `FootprintBar.poc` is `max(self.levels, key=...)`, which
  on a tie returns whichever price was *inserted first* — i.e. whichever
  printed first. Adopt `market_profile.Profile.poc`'s rule instead: nearest to
  the bar's mid, then price. Deterministic, reproducible across polls, and the
  same rule as the session POC on the same screen.
* **Single-row vs two-row expansion.** Classical Steidlmayer compares the sum
  of the *next two* rows each side; `market_profile.py` uses one. Stay with
  one for consistency, and say so in the payload.

**(iii)**
```jsonc
bar: { "poc": 77871.95, "vah": 77875.00, "val": 77868.00,
       "va_share": 0.71,          // share actually achieved — never assume 0.70
       "va_method": "single_row_alternating" }
level: { ..., "va": true }
```
`poc`/`vah`/`val` are `null` when the bar has fewer than 2 rows or zero volume.

**(iv)** Shade VA rows one step brighter than the volume heat, with a 1px cap
line at VAH and VAL per column. Keep the existing gold POC marker. **Do not
reuse gold for the VA** — the session VAH/VAL in `VolumeProfilePane` must stay
visually distinct from a 5-minute bar's VA, or a reader conflates the two.

**(v)** **Exact. The most reliable marker in the set.** POC and VA use only
total volume at price, which NSE publishes. They carry *no* aggressor
inference. Say so on the surface: this is the pair that survives a 60%
classified share, and telling the trader which markers survive a bad feed is
half the value of the confidence work. (Their exactness is contingent on §2 —
until `u` is counted, the VA is computed on a ladder that is missing volume.)

### 3.5 Delta divergence (session version present, bar version absent)

**(i)** Price makes a new extreme; the delta series does not confirm it.
Warning of exhaustion, never a reversal trigger on its own.

The codebase conflates two different objects:

* **Session/tick CVD divergence** — `OrderFlowTracker.divergence()`, over the
  last 120 CVD-curve points, split into halves. Keep as a session read.
* **Bar-level delta divergence** — the footprint version, over the last N
  bars. Missing. This is the one the chart needs.

**(ii)** Over the last `DIVERGENCE_LOOKBACK = 20` bars of the returned series:

```
price_span = max(h) - min(l);  cvd_span = max(cvd) - min(cvd)
price_edge = max(DIVERGENCE_EDGE_ROWS * row_size,
                 price_span * MIN_DIVERGENCE_FRACTION)     # DIVERGENCE_EDGE_ROWS = 2
cvd_edge   = cvd_span * MIN_DIVERGENCE_FRACTION            # reuse orderflow's 0.25

bearish := bars[-1].h == max(h over window)
           and bars[-1].h  >  max(h over window[:-1]) + price_edge
           and bars[-1].cvd <  max(cvd over window)  - cvd_edge
bullish := mirror on lows.
```

Two corrections against the existing session implementation, both of which
should also be applied to `orderflow.divergence`:

* **State the test as a new-extreme test.** The existing halving version asks
  "does the second half's extreme differ from the first half's", which is not
  the same question and fires whenever the extreme happens to fall late in the
  window. Require the extreme to be in the **most recent bar**.
* **The floor must be in ticks, not percent.** `orderflow.divergence` uses
  `price_low_1 * 0.002` — a percent-of-price floor, which is the exact
  instrument-dependence problem that `ABSORPTION_MAX_SPAN_TICKS` was
  introduced to fix, reintroduced two functions later. Use `row_size`
  multiples.

**(iii)**
```jsonc
payload["divergence_bars"] = {
  "kind": "bearish" | "bullish" | null,
  "at_bar": 1787824500, "reference_bar": 1787823600,
  "price_extreme": 77897.15, "reference_price_extreme": 77860.00,
  "cvd_at_extreme": 1460, "reference_cvd_extreme": 2310,
  "suppressed": false
}
```
Publish the **reference bar**, not just a kind. A divergence whose other leg
you cannot see is unfalsifiable.

**(iv)** Draw both legs: a line joining the reference bar's extreme to the
current bar's extreme across the price rows, and its mirror across the CVD
sub-pane (§3.7). Never a bare badge — a badge with no visible legs cannot be
checked by the person trading it.

**(v)** **Weak.** CVD is a running sum of inferred sides, so its extremes carry
accumulated classification error; and it is *anchor-dependent*, so until §3.7
is fixed the chart's divergence is not even computed on the same series as the
header's. Suppress at grade `low`.

### 3.6 Absorption and exhaustion — and how they differ

They are opposites. The codebase has one of them, and its gate is dead.

* **Absorption** — heavy aggressive volume that **fails to move price**. A
  large resting participant is filling the aggressors. *The passive side
  wins.* Read: fade the aggressor.
* **Exhaustion** — heavy aggressive volume that **moved price and then
  stopped**, because the aggressors ran out. The move dies on *thin* volume
  rather than being stopped by size. *Nobody wins; one side simply ends.* Read:
  the move is over, but there is no evidence of an opposing participant, so it
  is weaker than absorption.

**The discriminator is the volume at the failure point.** Absorption = big
volume, no progress. Exhaustion = small volume at the extreme, after progress.
They are also mutually exclusive at the same extreme, and that is a real check
(see below), not a nicety.

**(ii) Absorption, per row `k` in bar `B`:**

```
v[k]   = bid[k] + ask[k] + u[k]
d[k]   = ask[k] - bid[k]
v[k] >= ABSORB_VOLUME_MULT * median(v over B's populated rows)   # 2.0
and |d[k]| / v[k] >= ABSORPTION_MIN_PRESSURE                     # 0.35, reuse orderflow's
and B did not settle beyond row k in the direction of that pressure:
      d[k] > 0  ->  B.c <= price(k) + row_size
      d[k] < 0  ->  B.c >= price(k) - row_size
side = "buyers_absorbed" if d[k] > 0 else "sellers_absorbed"
```
Naming is from the **aggressor's** point of view, matching `orderflow.py`:
"buyers_absorbed" means a seller absorbed the buyers.

**(ii) Exhaustion, at bar `B`'s extreme:**

```
k = top row if B.c >= B.o else bottom row
v[k] <= EXHAUST_VOLUME_FRACTION * v[poc_row]                     # 0.25
and the two rows adjacent inward each have volume >= v[k]        # tapering into the extreme
and B spans >= EXHAUST_MIN_RANGE_ROWS rows                       # 4 — there was a move to exhaust
and B.unfinished[that end] == "finished"                         # the auction DID complete
```
That last clause is where the two markers meet: an exhausted high has zero ask
at the top row (buyers stopped paying up); an unfinished high has both sides.
A bar cannot be both at the same end.

New constants in `footprint.py`: `ABSORB_VOLUME_MULT = 2.0`,
`EXHAUST_VOLUME_FRACTION = 0.25`, `EXHAUST_MIN_RANGE_ROWS = 4`.

**(iii)**
```jsonc
bar["absorption"] = [ { "k": 1557438, "price": 77871.90, "side": "buyers_absorbed",
                        "volume": 640, "pressure": 0.72, "suppressed": false } ]
bar["exhaustion"] = { "end": "high", "price": 77897.15, "volume": 30,
                      "ratio": 0.09, "volume_test": true, "auction_test": true,
                      "suppressed": false } | null
```
Publish `volume_test` and `auction_test` separately so the surface can honestly
say "volume-exhausted, auction status unknown" when the extreme row has no
classifiable volume.

**(iv)** Absorption renders as a filled block on the **passive** side of the
divider — a `buyers_absorbed` row gets a *sell*-coloured block, because the
seller is who won — with a thin outline. This is deliberately the opposite
side from the imbalance tint (§3.1) so the two are never confused; they are
near-opposite readings and today's palette would make them look alike.
Exhaustion renders as a hollow caret at the bar extreme. Both must also appear
as **text in the crosshair readout**, never colour alone.

**(v)**
* Absorption: **moderate.** The volume term is exact; the pressure term is
  inferred. The "big volume, no progress" shape is carried mostly by the exact
  half. Always publish `pressure` beside the flag so the reader can weight it.
* Exhaustion: **good on the volume test, weak on the auction clause.** The
  taper uses totals only (exact); the finished/unfinished gate is one inferred
  row. Hence the two separate booleans.
* **Session-level `flow.absorption` is currently unusable on any high-priced
  instrument.** Measured live on SENSEX futures: `span_ticks: 1943.0` against
  a `<= 4.0` gate. Fix via §1.4 (per-symbol tick size), **not** by loosening
  the gate — the 4-tick gate is correct and was measured.

### 3.7 Cumulative delta and its anchor — the known defect, quantified

**(i)** CVD is the running sum of signed aggressive volume since a reference
point. Its meaning is entirely a function of that reference. Two CVD numbers
with different anchors on one screen are not two views of one thing; they are
two different quantities sharing a label.

**The defect, precisely:**

* `FootprintBook._cvd[symbol]` is set to `0.0` in `watch()` and accumulates
  only what the book is subsequently fed: the seed replay (`FlowState.recent`,
  `maxlen=400`) plus live prints.
* `FlowState.cumulative_delta` accumulates every print since session open, and
  is restored across restarts.
* `FootprintChart.tsx:390-393` prints `tail.cvd` labelled **"CVD"**.
  `OrderFlowWorkspace.tsx:84` prints `flow.cumulative_delta` labelled
  **"Session CVD"**. Different objects, adjacent on screen.

Measured, live, BSE:SENSEX26SEPFUT, 2026-08-27:

```
flow.cumulative_delta   16,840
last bar cvd             1,460      (8.7% of it)
```

The brief's "~25%" understates it. The error is **unbounded**: it is a
function of how long ago the chart was opened and how much of the session the
400-print seed covers — on a liquid contract, minutes. Two further instances of
the same bug: a symbol evicted by `MAX_DETAIL_SYMBOLS = 16` and reopened
re-anchors at zero; and `FootprintBar.cvd`'s own docstring already *claims*
"session cumulative delta as of this bar's close", which is the correct
behaviour and is not what the code does.

**(ii) Required behaviour and the fix.** CVD is a **session-anchored** series;
every bar's `cvd` is the session cumulative delta as of that bar's last print.

```python
def watch(self, symbol, seed_prints=None, session_delta=None, tick_size=None):
    ...
    seed = list(seed_prints or ())
    seeded = sum(p.size if p.side > 0 else -p.size if p.side < 0 else 0 for p in seed)
    # Anchor so that after replaying the seed the running CVD EQUALS the
    # session figure the flow tracker holds. Bars before the seed window do
    # not exist; the bars that do must carry the session number, not a
    # window-local rebase.
    self._cvd[symbol] = (session_delta - seeded) if session_delta is not None else 0.0
    self._cvd_basis[symbol] = "session" if session_delta is not None else "watch_window"
    for row in seed:
        self.on_print(symbol, row.timestamp, row.price, row.size, row.side)
```

`app.mp_footprint` passes `session_delta=seed.cumulative_delta` whenever a
`FlowState` exists. `watch()` keeps its existing early return for an
already-watched symbol — do not re-anchor a live chart.

**(iii)**
```jsonc
payload["cvd_basis"] = "session" | "watch_window"
payload["cvd_anchor"] = 15380.0                  // CVD immediately before the first seeded print
payload["session_cumulative_delta"] = 16840.0    // for on-screen reconciliation
payload["cvd_band"] = 2710.0                     // see below
```

`cvd_band` is a **hard bound, not a model**: it is
`flow.unclassified_volume` — the exact amount by which CVD could differ if
every sideless print had in fact gone one way. `null` when
`unclassified_volume` is unmeasured. This is the honest uncertainty statement
for a cumulative inferred quantity, and it costs nothing to compute.

**(iv)** A dedicated **CVD sub-pane** below the delta footer (toggleable with
it), a stepped line on the same x-grid as the columns, with a zero line and,
optionally, a ±`cvd_band` envelope at low opacity. Rules:

* The header number and the pane must read from **the same series**. One
  number, one anchor, one label.
* When `cvd_basis == "watch_window"` the pane draws a dashed baseline and the
  label reads **"CVD (window)"** — never a bare "CVD".
* The label is `"CVD (est.)"` in the anchored case. This is not decoration; a
  desk that reads inferred numbers as measured ones sizes off guesses.
* `OrderFlowWorkspace`'s "Session CVD" stat must display
  `payload.session_cumulative_delta` and, when it differs from the chart's
  tail by more than one row's volume, render a reconciliation warning rather
  than two silently disagreeing numbers.

**(v)** **Weak in level, moderate in shape.** The absolute level is the sum of
thousands of inferred sides and drifts monotonically — error accumulates, it
does not average out. What survives is the *slope* and the *zero crossings*.
Render CVD as a shape and read divergences from it; never quote a CVD level as
a measured order imbalance. Always render `classified_share` and `cvd_band`
adjacent to it.

### 3.8 Single prints and low-volume nodes (absent at bar scale)

**(i)** Two distinct things; do not conflate them.

* **Single print (market-profile sense)** — a price touched in exactly one TPO
  bracket. Already exists as `Profile.single_prints` (session scale) and is
  already in the payload's `profile` block.
* **Low-volume node (footprint sense)** — a row whose total volume is a local
  minimum and far below the bar's typical row. A price the auction rejected
  quickly; it tends to be revisited and to act as a fast-travel zone.

**(ii) LVN, per bar:**

```
v[k] = bid[k] + ask[k] + u[k]
lvn(k) := v[k] <= LVN_FRACTION * v[poc_row]              # LVN_FRACTION = 0.15
          and v[k] <= v[k-1] and v[k] <= v[k+1]          # local minimum;
                                                         # a MISSING neighbour fails the test
          and k != poc_row
          and the bar has >= LVN_MIN_ROWS populated rows # 6
Collapse contiguous lvn rows into one zone.
```

**(ii) Footprint single prints, across the returned window** — this is the
genuine "a fast move left a gap" marker and is cheap at read time, since
`payload()` already sees the whole series: a row `k` with `v > 0` that is
populated in **exactly one bar** of the returned window.

**(iii)**
```jsonc
bar["lvn"] = [ { "k_from": 1557420, "k_to": 1557421, "rows": 2,
                 "from": 77871.00, "to": 77871.05, "volume": 15 } ]
level: { ..., "lvn": true }
payload["single_print_rows"] = [77800.00, 77805.00]
payload["single_print_window_bars"] = 8
```

`single_print_window_bars` is **not optional**. "Single print" over 8 bars and
over 80 bars are different claims, and a reader who cannot see the
denominator cannot evaluate either.

**(iv)** LVN rows: suppress the volume heat and draw a hairline strikethrough
across the full cluster width, so an absence reads as an absence rather than
as an empty row. Session single-print prices: a thin horizontal line across
the whole chart, distinct in dash pattern from the LTP line
(`FootprintChart.tsx:352-370`). **Do not signal an LVN by colour intensity
alone** — an LVN is defined by absence, and the eye reads absence badly at 1px
rows.

**(v)** **Exact — no inference anywhere.** Both tests use only total volume per
row. Tag them `basis: "volume"` and let them keep full saturation even at
confidence grade `low`. When the flow feed is poor, these and the POC/VA are
what the trader still has.

### 3.9 The confidence overlay

**(i)** What already exists per symbol, and what each actually means:

| field | definition | pitfall |
|---|---|---|
| `flow.classified_share` | `(total_volume − unclassified_volume) / total_volume` — share of **tape volume** given any side. `None` when unmeasured. | already correct; `None` must render as *nothing*, never as 0% |
| `flow.quote_share` | `methods["quote"] / trades` — share of **prints** resolved by the strong rule | a **count** share, not a volume share, so it is not directly comparable to `classified_share` |
| `flow.depth_ticks` | `quotes_seen` — prints that had both a bid and an ask available | the field that distinguishes "no depth" from "depth present but the print was inside the spread" — and nothing on screen uses it |

Measured live on SENSEX26SEPFUT: `depth_ticks 416 == trades 416` — full depth
coverage — yet `quote_share 0.606`, because 144 prints landed inside the
spread (mid rule) and 20 fell to the tick rule. A surface that shows only
`quote_share` invites the reader to conclude the feed was thin when it was
perfect. **Publish `depth_share = quotes_seen / trades` alongside.**

**(ii) The core position: confidence is per marker, not per symbol.** One
percentage cannot express that the per-bar POC three rows down is exact while
the stacked imbalance above it is a conjunction of eight inferences. Publish
the basis **structurally**, so the client cannot get it wrong:

```jsonc
payload["basis"] = {
  "volume":   ["v", "poc", "vah", "val", "lvn", "single_prints", "u"],
  "inferred": ["bid", "ask", "d", "delta", "cvd", "imb", "stacks",
               "unfinished", "absorption", "exhaustion", "divergence"]
}
payload["confidence"] = {
  "classified_share": 0.914,          // or null when unmeasured
  "quote_share": 0.606,
  "depth_share": 1.0,
  "method_mix": { "quote": 252, "mid": 144, "tick": 20, "zero_tick": 0 },
  "grade": "high" | "fair" | "low" | null,
  "avg_spread_ticks": null            // reserved: tick_store.tick_minute_flow.avg_spread
}
bar["methods"] = { "quote": 31, "mid": 12, "tick": 2, "zero_tick": 0 }
bar["classified_share"] = 0.96        // or null
```

Grade thresholds — specified here, not left to the UI:

```
high : classified_share >= 0.90 and quote_share >= 0.60
fair : classified_share >= 0.75 and quote_share >= 0.35
low  : otherwise
null : classified_share is None (never measured) -> render NOTHING
```

The `null` case is load-bearing. A session restored from a pre-fix payload has
no coverage measurement at all, and rendering `"low"` there would be a claim
about a measurement nobody took — the same failure the `classified_share =
None` work exists to remove.

**(iii)/(iv) How a professional surface communicates a weak inference.** Six
rules, in order of importance:

1. **A persistent grade chip in the chart header, not a stat buried in a
   toolbar row.** `"flow: fair · 81% classified · 44% quote-ruled"`, coloured.
   It belongs beside the CVD number, because that number is what it qualifies.
   Today the two coverage stats sit at the end of a seven-stat strip
   (`OrderFlowWorkspace.tsx:92-99`) where they read as trivia rather than as a
   caveat attached to everything to their left.
2. **Desaturate the inferred layer at grade `low` — do not hide it.** Bid/ask
   numbers stay fully legible in monochrome; the delta spine, imbalance tints,
   stack ribbons and CVD line drop to ~35% opacity; the chip turns red. The
   reader still sees every number, and can see that the desk does not stand
   behind the colour. Hiding data teaches the reader nothing; desaturating
   teaches them exactly what is weak.
3. **Suppress compound markers at grade `low`.** Single-row imbalance may still
   render — one inference. Stacked imbalance, absorption, exhaustion and
   divergence must not render as badges; their false-positive rate rises
   superlinearly with per-print error. Emit them with `"suppressed": true` and
   a reason, so the API stays honest and the surface stays quiet.
4. **Per-bar coverage, not only per-symbol.** A bar captured during a depth
   outage is not the session average. `bar["classified_share"]` costs four
   integers per bar. When a bar's own share is more than 15 points below the
   session's, stripe that column's background at ~4% opacity. That is how a
   reader spots the one bar in the series they should not trade off.
5. **Never a bare inferred number without its residual within reach.** The
   crosshair readout gains a `Resid` row whenever the hovered row or bar has
   `u > 0`, and the CVD readout carries `± cvd_band`.
6. **Estimator words on screen.** "Δ (est.)", "CVD (est.)", "inferred
   aggressor". The tooltip on the grade chip states the constraint in one
   sentence: *NSE does not publish the aggressor; sides are inferred by quote
   rule, then mid, then tick.*

**(v) Reliability of the confidence layer itself.** `classified_share` is
exactly measurable and should be trusted as such. `quote_share` is exactly
measurable but is a *proxy* for quality, not quality: a 100%-quote-share
symbol with a 20-tick spread is a worse read than a 60%-quote-share symbol
with a 1-tick spread. `tick_store.tick_minute_flow.avg_spread` already exists
and should feed a spread term into the grade in a later pass; `avg_spread_ticks`
is reserved above so the shape does not have to change again.

---

## 4. Consolidated payload contract (`spec_version: 1`)

Additions and changes to `FootprintBook.payload()`. Existing keys keep their
names and meanings unless marked.

```jsonc
{
  "spec_version": 1,
  "symbol": "BSE:SENSEX26SEPFUT",
  "timeframe_seconds": 300,
  "capture_timeframe_seconds": 60,

  "tick_size": 0.05,                    // NOW per symbol (§1.4)
  "row_ticks": 5,                       // NEW
  "row_size": 0.25,                     // NEW = tick_size * row_ticks
  "imbalance_ratio": 3.0,
  "stacked_imbalance_min": 3,           // NEW

  "cvd_basis": "session",               // NEW  "session" | "watch_window"
  "cvd_anchor": 15380.0,                // NEW
  "session_cumulative_delta": 16840.0,  // NEW
  "cvd_band": 2710.0,                   // NEW  null when unmeasured

  "divergence_bars": { ... },           // NEW  §3.5
  "single_print_rows": [ ... ],         // NEW  §3.8
  "single_print_window_bars": 8,        // NEW

  "confidence": { ... },                // NEW  §3.9
  "basis": { "volume": [...], "inferred": [...] },   // NEW

  "coverage": {                         // NEW  §5-A12
    "first_bar_t": 1787824200,
    "seeded_prints": 60,
    "seed_truncated": true
  },

  "bars": [ {
    "t": 1787824500,
    "o": 77875.0, "h": 77897.15, "l": 77800.0, "c": 77814.1,   // RAW prices (§1.5)
    "v": 1980,
    "u": 80,                            // NEW  unclassified volume (§2)
    "delta": 1180,
    "cvd": 16840,                       // NOW session-anchored (§3.7)
    "poc": 77871.95,
    "vah": 77875.00, "val": 77868.00,   // NEW
    "va_share": 0.71,                   // NEW
    "va_method": "single_row_alternating",   // NEW
    "imb_floor": 25,                    // NEW
    "rows": 20,                         // NEW  populated row count
    "methods": { "quote": 31, "mid": 12, "tick": 2, "zero_tick": 0 },  // NEW
    "classified_share": 0.96,           // NEW  null when unmeasured
    "stacks": [ ... ],                  // NEW  §3.2
    "unfinished": { ... },              // NEW  §3.3
    "absorption": [ ... ],              // NEW  §3.6
    "exhaustion": { ... } | null,       // NEW  §3.6
    "lvn": [ ... ],                     // NEW  §3.8
    "levels": [ {
      "k": 1557439,                     // NEW  integer row index (§1.2)
      "p": 77872.00,
      "bid": 40, "ask": 180,
      "u": 0,                           // NEW  (§2)
      "d": 140,
      "imb": "buy",                     // dominant side; kept for old clients
      "imb_buy": true,                  // NEW
      "imb_sell": false,                // NEW
      "imb_ratio": 4.5,                 // NEW  null when the neighbour row is empty
      "poc": false,
      "va": true,                       // NEW
      "lvn": false                      // NEW
    } ]
  } ],

  "dom": null,
  "tape": [ ... ],
  "profile": { ... },
  "flow": { ... },
  "setup": { ... },
  "watching": [ ... ],
  "subscribed_now": false
}
```

New constants, all in `footprint.py` beside the existing `IMBALANCE_RATIO`:

```python
STACKED_IMBALANCE_MIN     = 3
IMBALANCE_VOLUME_FRACTION = 0.25
UNFINISHED_MIN_FRACTION   = 0.02
ABSORB_VOLUME_MULT        = 2.0
EXHAUST_VOLUME_FRACTION   = 0.25
EXHAUST_MIN_RANGE_ROWS    = 4
LVN_FRACTION              = 0.15
LVN_MIN_ROWS              = 6
DIVERGENCE_LOOKBACK       = 20
DIVERGENCE_EDGE_ROWS      = 2
ROW_TICKS_LADDER          = (1, 2, 5, 10, 20, 50, 100, 200, 500, 1000)
ROW_TARGET_ROWS           = 30
```

Import, do not redefine: `VALUE_AREA_FRACTION` from `market_profile`,
`ABSORPTION_MIN_PRESSURE` and `MIN_DIVERGENCE_FRACTION` from `orderflow`.
Two copies of a threshold is how the two surfaces on this screen end up
disagreeing.

---

## 5. What in the current implementation is not professional-grade

Every item is a defect, with its evidence. A1, A4 and A7 are blocking.

**A1 — The price grid is the raw exchange tick, so the chart is unreadable on
any wide-range instrument, and imbalance cannot fire.** Measured: 1,944 grid
rows for 39 populated levels; `rowH ≈ 0.23px`; zero of 39 levels flagged. See
§1.1. Adjacent rows are essentially never both populated, so the diagonal
lookup finds nothing. *Every other marker in this spec depends on fixing
this.*

**A2 — Diagonal imbalance is also dead on 0.10-tick instruments, for a second
and independent reason.** `footprint.py:92` looks up `P − 0.05`, where no
trade can ever occur. The audit's finding. §1.4 fixes it; §1.1 fixes the
other. Both are needed — neither fix subsumes the other.

**A3 — Levels are keyed by float price and neighbours found by float
arithmetic.** `round(price - tick_size, 2)` hard-codes 2 decimal places and is
fragile besides; the codebase already documents this exact bug class in
`orderflow.absorption`. Key by integer row index (§1.2).

**A4 — The bar's ladder silently loses unclassified volume and publishes no
residual.** Measured: `v = 1980` vs `Σ(bid+ask) = 1900`, gap 80, matching the
one `side: 0` print in that bar's tape. The conservation fix landed in
`FlowState` and was never carried into `FootprintBar`. §2.

**A5 — The session confidence figure contradicts the ladder beneath it.**
`flow.unclassified: 4` with `unclassified_volume: 0.0` and
`classified_share: 1.0`, on a bar with 80 sideless lots. (`methods` has no
`zero_tick`, so the deployed image predates the 2026-08-27 fix.) Add the
invariant test regardless of cause: `unclassified > 0 ⟹ unclassified_volume > 0`.

**A6 — Absorption's tick gate is unpassable on high-priced instruments.**
`absorption.span_ticks: 1943.0` against `<= 4.0`, because
`OrderFlowTracker.tick_size` is fixed at 0.05. Fix the denominator, keep the
gate. §1.4.

**A7 — CVD has two anchors and two labels on one screen.** 1,460 vs 16,840 on
the same symbol at the same instant. §3.7.

**A8 — `imb` is a single-valued `elif`, so a row can only be one thing.**
`footprint.py:96-100` discards the sell finding on a row that satisfies both
diagonals. They are independent tests of independent cells. §3.1.

**A9 — A zero or absent neighbour is treated as "no imbalance" when it is the
strongest imbalance there is.** The `below[0] > 0` / `above[1] > 0` guards skip
the infinite-ratio case that every platform flags. §3.1.

**A10 — `MIN_IMBALANCE_VOLUME = 20` is an absolute lot count applied to every
instrument**, from sub-lot on a NIFTY future to material on a thin option.
Scale it to the bar's own median row volume and publish the effective floor.
§3.1.

**A11 — `FootprintBar.poc` breaks ties by dict insertion order**, i.e. by
whichever price printed first. `market_profile.Profile.poc` already solved this
(proximity to centre, then price). Two POCs on one screen should be computed
the same way. §3.4.

**A12 — The capture window is 400 prints deep and the chart does not say so.**
`FlowState.recent` is `maxlen=400`; after a restart the persisted slice is only
the last 60 (`mp_engine.py`, `list(state.recent)[-60:]`). A chart opened at
14:00 shows a few minutes of clusters and looks identical to one that captured
all day. Publish `coverage` (§4) and render "clusters from 13:57" in the
header. `tick_store.tick_minute_flow` can backfill earlier bars' OHLCV, delta
and CVD, but **not** the per-price ladder — the store keeps only a per-session
ladder (`tick_session_ladder`), not a per-minute one. Backfilled bars must be
marked `"levels_available": false` and drawn as plain delta columns. **Do not
synthesise levels.**

**A13 — `payload()` recomputes every imbalance for every level of every bar on
every poll.** `OrderFlowWorkspace` polls at 3s with up to 80 bars across up to
16 watched symbols. Closed bars never change: cache the computed rows on the
bar, invalidate only the live one. Not a correctness bug; it is the difference
between a desk that stays responsive and one that does not.

**A14 — Time bars only.** A 5-minute bar at 09:20 and one at 13:30 are not
comparable objects; professional footprint work uses volume, tick and range
bars. Out of scope for this pass — but `payload()`'s read-side aggregation is
already the right place for it, so `timeframe_seconds` should become a
`bar_type` + `bar_size` pair rather than being widened further.

**A15 — DOM is touch-only and empty until a quote arrives after `watch()`.**
`on_quote` returns early for unwatched symbols; the live payload returns
`dom: null`. The pane must say "no depth captured since this chart opened"
rather than rendering an empty ladder that reads as "no bids".

**A16 — Bar OHLC is built from bucketed prices.** `bar.add(self.bucket(price),
…)` — harmless at 0.05-on-0.05, materially wrong once rows aggregate, and it
would make the unfinished-auction test key off a quantised extreme. §1.5.

---

## 6. Build order and required tests

Order is a dependency chain, not a preference:

1. §1.4 per-symbol tick size → §1.2 integer row keys → §1.3 `row_ticks`
2. §2 conservation (`u` on level and bar)
3. §3.7 CVD anchor
4. §3.4 VA/POC, §3.8 LVN/single prints  *(the exact markers — ship these first;
   they are correct regardless of feed quality)*
5. §3.1 imbalance repair → §3.2 stacks → §3.3 unfinished → §3.6 absorption/
   exhaustion → §3.5 divergence
6. §3.9 confidence overlay — but the `basis` map and `grade` must land **with**
   step 4, not after it, or the first inferred marker ships unqualified

Tests in `tests/test_footprint.py` (every new behaviour needs one — house rule):

* `Σ(bid + ask + u) == v` for every bar, including after `merge()`
* row grid: a 0.10-tick instrument flags a diagonal imbalance (today it cannot)
* row grid: a wide-range instrument with `row_ticks > 1` produces contiguous
  populated rows and fires an imbalance
* neighbour lookup by integer index survives a price where float arithmetic
  drifts (use the documented 100.20 − 100.00 case)
* stacked imbalance: 3 consecutive same-side rows produce one stack; a gap row
  in the middle produces none; an empty row does not count as imbalanced
* unfinished auction: both-sides-at-the-top → `"unfinished"`; zero ask at top
  → `"finished"`; minority side below the floor → `"weak"`; extreme row wholly
  unclassified → `null`
* value area: a bar whose rows match a hand-computed profile returns the same
  VAH/VAL as `market_profile.Profile.value_area` on the same counts
* POC tie-break is deterministic and matches `market_profile`'s rule
* **CVD anchor:** `watch(..., session_delta=X)` then replay → `bars[-1]["cvd"]
  == X`; with `session_delta=None` → `cvd_basis == "watch_window"`
* absorption fires on a high-priced instrument once tick size is per symbol
  (regression for `span_ticks: 1943.0`)
* exhaustion and unfinished are never both true at the same end
* confidence: `classified_share is None` → `grade is None`, and no percentage
  is emitted
* invariant: `unclassified > 0 ⟹ unclassified_volume > 0` (in
  `tests/test_orderflow_conservation.py`)
* every marker whose `basis` is `"inferred"` carries `"suppressed": true` at
  grade `low`; every `"volume"`-basis marker does not

Run: `cd "/Users/ramachandran/CLAUDE PROJECTS/MACD mini" && PYTHONPATH=src
"/Users/ramachandran/CLAUDE PROJECTS/Nomad Curie/TradeBot/.venv/bin/python" -m
pytest tests/ -q`

---

## 7. One rule that overrides everything above

If a quantity is unmeasured, publish `null` and let the surface omit it. Never
`0`, never a default, never a synthesised share. A footprint can never tie out
to exchange volume, because NSE does not report the aggressor — but the
**residual is a number this desk can show**, and showing it is what separates
an order-flow surface a professional will trust from one that merely looks
like the platforms they already use.
