# Profit-taking implementation review, 9 October 2026

Scope: read-only review of the MACD, Blast, Smart MACD/Blast, and Market Profile
exit paths. No production source, settings, holdings, orders, or ledgers were
modified. The root reviewer confirmed checkout/container hash parity for the
eight relevant source modules. No applicable AGENTS.md was found in this checkout
or its parent directories. Line references below target the reviewed checkout.

## Findings that should precede an exit-policy experiment

### P1 — Smart restarts forget which profit slices have already sold

`SmartLane._restore()` merges excursion prices/returns but omits the persisted
`exit_stage` (and entry anchor/stage). The dataclass default therefore becomes
zero after replay, although the fill checkpoint durably saved stage 1. The next
tick above +30% sells the first slice again. Repeated restarts can continue
changing the exit schedule. This is an implementation defect, independent of
whether the +30/+50/+75% ladder is a good strategy.

- Restore omission: `/Users/ramachandran/MACD-mini-ADV/engine/src/macd_trader/smart_lane.py:311` through 326.
- Stage-dependent sale: `/Users/ramachandran/MACD-mini-ADV/engine/src/macd_trader/smart_lane.py:208` through 212.
- Durable fields: `/Users/ramachandran/MACD-mini-ADV/engine/src/macd_trader/repository.py:431` through 446.
- Correct MACD comparison: `/Users/ramachandran/MACD-mini-ADV/engine/src/macd_trader/engine.py:658` through 663.

Disposable reproduction: 10 lots at 10; tick 13.1 sells one, leaving 9 lots at
stage 1. Saved stage is 1; restart restores 9 lots at stage 0. Another tick at
13.1 leaves 8 lots and two `STAGED_PROFIT_EXIT_1` records.

### P1 — Blast can lose its armed trail and peak after an unclean restart

Blast fills checkpoint position excursions, and graceful `BlastEngine.stop()`
also saves them. The periodic maintenance loop flushes journal watchers but does
not checkpoint the actual Blast positions. A crash, force restart, or interrupted
shutdown after a favorable move can restore the last fill's high and disarm an
already armed trail. This is an implementation defect. The ordinary MACD lane
checkpoints every approximately five seconds of strategy-candle activity; Smart
checkpoints on its approximately 30-second loop.

- Blast maintenance loop: `/Users/ramachandran/MACD-mini-ADV/engine/src/macd_trader/engine.py:1736` through 1749.
- Blast shutdown-only explicit excursion save: `/Users/ramachandran/MACD-mini-ADV/engine/src/macd_trader/blast_engine.py:1061` through 1067.
- Blast restoration: `/Users/ramachandran/MACD-mini-ADV/engine/src/macd_trader/blast_engine.py:408` through 424.
- MACD checkpoint: `/Users/ramachandran/MACD-mini-ADV/engine/src/macd_trader/engine.py:1503` through 1506.
- Smart checkpoint: `/Users/ramachandran/MACD-mini-ADV/engine/src/macd_trader/smart_lane.py:514` through 525.

Disposable reproduction: enter 10, tick 20 arms a 12 trail. Run the same
`flush_watchers()` called by maintenance, then simulate an unclean exit. The
checkpoint still has max_price 10; restoration produces peak 10 and no trail.
A subsequent price 11 would have exited before the restart and would not exit
afterward.

### P2 — Restoration promotes valuation-only marks to executable trailing highs

The code deliberately distinguishes `peak_price` (session-gated execution) from
`max_price` (valuation, including startup and off-session marks). However, MACD,
Blast, and Smart restore `peak_price` from `max_price`, and the checkpoint does
not store a separate executable high. An off-session/REST valuation mark can
therefore arm or tighten risk only after a restart. This also weakens the use of
persisted MFE as research evidence of executable favorable excursion.

- Intended distinction: `/Users/ramachandran/MACD-mini-ADV/engine/src/macd_trader/models.py:126` through 130.
- Off-session marks: `/Users/ramachandran/MACD-mini-ADV/engine/src/macd_trader/engine.py:1485` through 1491.
- MACD restore promotion: `/Users/ramachandran/MACD-mini-ADV/engine/src/macd_trader/engine.py:664` through 673.
- Blast promotion: `/Users/ramachandran/MACD-mini-ADV/engine/src/macd_trader/blast_engine.py:417` through 424.
- Smart promotion: `/Users/ramachandran/MACD-mini-ADV/engine/src/macd_trader/smart_lane.py:319` through 326.

Disposable reproduction: enter 100; executable tick 140 arms trail 105. A
valuation-only mark 200 leaves the execution peak unchanged until restart;
restoration changes peak to 200 and trail to 150. Price 130 would then exit only
in the restarted process. Preserving the actual executable high separately would
make the before/after behavior consistent.

## Current policy and what an extreme-move overlay would change

These are strategy/modeling choices, not proven defects or evidence that a new
policy improves returns. Defaults are listed; root runtime review must establish
the actual saved settings.

| Lane/source | Current tick exits | Partial handling | Bar exit |
|---|---|---|---|
| MACD | Hard -30%; trail activates +30%, stays 25% below peak, floored at modeled breakeven | +30/+50/+75% above current average sells one exchange lot per stage, retains last lot | Optional negative-MACD invalidation if lifetime MFE never reached 10%; off by default |
| Blast | Hard -50%; trail activates +30%, stays 40% below peak, floored at modeled breakeven | None | None |
| Smart, MACD source | Expiry; hard -40%; trail activates +40%, stays 30% below peak, floored at modeled breakeven; all out +100%; four-session limit | Same three stages, one lot per stage | Negative-MACD invalidation, MFE <10% |
| Smart, Blast source | Expiry; hard -50%; trail activates +30%, stays 40% below peak, floored at modeled breakeven | None; no +100% target or hold limit | None |
| MP | Hard -25%; trail activates +20%, stays 20% below peak, floored at modeled breakeven; EOD carry decision; adverse underlying absorption beyond value | Full quantity per automatic exit | None |

References: MACD `/Users/ramachandran/MACD-mini-ADV/engine/src/macd_trader/execution.py:317`,
`:402`, `:426`, `:447`, `:471`; Smart
`/Users/ramachandran/MACD-mini-ADV/engine/src/macd_trader/smart_lane.py:153`, `:183`,
`:192`, `:208`, `:214`, `:262`; Blast
`/Users/ramachandran/MACD-mini-ADV/engine/src/macd_trader/blast_engine.py:207`, `:238`;
MP `/Users/ramachandran/MACD-mini-ADV/engine/src/macd_trader/mp_engine.py:963`; defaults
`/Users/ramachandran/MACD-mini-ADV/engine/src/macd_trader/config.py:79`, `:113`, `:207`,
`:232`.

### MACD can pyramid during an extreme jump before harvesting

The MACD tick path prioritizes hard/trailing stops, then a one-lot pyramid add
followed by immediate return, then a one-lot staged sale. Add thresholds are
relative to the original entry anchor; sale thresholds use the changing average
cost. Repeated ticks during a large jump can buy all remaining pyramid lots near
the peak, raise average cost, and prevent any profit stage from qualifying.

Disposable deterministic policy example, zero costs: one 100-unit lot at 100;
three ticks at 200 add one lot each, producing four lots at average 175 and no
profit-taking slices. Tick 150 hits the earlier trail and realizes -10,000, even
though the original entry doubled. This is a policy interaction, not a claim
about its frequency or the expected gain from changing it.

Reference: `/Users/ramachandran/MACD-mini-ADV/engine/src/macd_trader/execution.py:447`
through 476; average-cost update in
`/Users/ramachandran/MACD-mini-ADV/engine/src/macd_trader/portfolio.py:138` through 146.

### Fixed one-lot sales harvest a smaller fraction of larger tickets

Each stage sells exactly one lot, not a fraction of entry or remaining quantity.
A 100-lot Smart MACD position banks only three lots over the three stages before
its target/trail exits the remaining 97. A two-lot position can use only one
partial stage because the last lot is retained. Research comparing profit capture
must normalize initial lots and capital and model the final residual holding.
This is a strategy choice. Smart MACD's full +100% target is evaluated before the
partial ladder, so a gap to/above +100% exits the whole holding immediately.

### Wide price trails allow substantial profit giveback

For a position at +100% premium, a 25% price trail preserves +50% above entry, a
30% trail preserves +40%, and a 40% trail preserves +20%, before fees, spread,
and gap-through slippage. Those give back 50%, 60%, and 80% of the open gain,
respectively. At only +30%/+40%, the modeled breakeven floor can preserve almost
none of the gain. A price-percent trail is not a fraction-of-profit lock. These
are arithmetic consequences of the existing policy; they do not establish that
tighter exits improve total returns.

### MP already has an extreme/exhaustion rule; other lanes do not

MP uses the *underlying's* above-value buyer absorption for calls and below-value
seller absorption for puts. It fully exits even without a minimum profit gate;
it is therefore a structural exit, not specifically a winner-harvesting rule.
The MACD/Blast/Smart paths do not currently tighten based on own-history return
percentiles, ATR-normalized extension, speed, volume climax, failed highs, or
fraction-of-MFE giveback.

Reference: `/Users/ramachandran/MACD-mini-ADV/engine/src/macd_trader/mp_engine.py:991`
through 1009.

## Cadence, freshness, and paper-model limitations

- Protective and staged exits act on each accepted in-session option tick; MACD
  invalidation acts only on the newly closed strategy-timeframe candle. Ladders
  execute one stage per tick, rather than batching every threshold crossed by a
  jump. All these exits depend on a tick arriving for the held contract.
- Engine admission rejects future-skewed and out-of-order exchange timestamps and
  requires the print and local wall time to belong to the same instrument session.
  It deliberately treats receipt time as executable quote freshness; within-day
  delayed exchange timestamps may still qualify. The shared manager requires a
  finite positive quote and age within the configured default 10 seconds.
- Stops are local trigger conditions followed by a market sale at the next
  received last price, not guaranteed fills at the stop. Gap-through losses are
  possible even with the modeled breakeven floor.
- Ordinary MACD/Blast/Smart paper fills use last price plus/minus fixed slippage
  and zero explicit brokerage. MP models fixed slippage plus brokerage. There is
  no bid/ask spread, market-depth or price-impact model in these exit fills. This
  is especially material when proposing harvesting at rapid or illiquid extremes.
- Turning `blast_enabled` off also bypasses Blast's protective tick execution;
  it is not merely an entry pause. This deserves a separate operational review
  when a Blast book remains open. MP explicitly continues managing held positions
  even when new entries are disabled.

References: `/Users/ramachandran/MACD-mini-ADV/engine/src/macd_trader/engine.py:1427`
through 1443 and `:1466` through 1477;
`/Users/ramachandran/MACD-mini-ADV/engine/src/macd_trader/execution.py:248` through
276, `:368` through 375;
`/Users/ramachandran/MACD-mini-ADV/engine/src/macd_trader/blast_engine.py:902` through
908; `/Users/ramachandran/MACD-mini-ADV/engine/src/macd_trader/mp_engine.py:732`,
`:759`, `:1061` through 1067.

## Reproduction evidence

`reproduce_exit_review.py` ran against the currently deployed container's matching
source, using four isolated temporary databases and a broker stub that raises on
any order API call. `reproduction_results.jsonl` contains the four observed
results. Run from the repository root:

```sh
docker exec -i macd-mini-parallel-engine-1 python - < research/extreme_profit_taking_20261009/code_review/reproduce_exit_review.py
```

The first three cases demonstrate persistence defects. The fourth demonstrates a
deterministic consequence of the current policy, and should be evaluated in a
capital-matched replay before a policy change is proposed.
