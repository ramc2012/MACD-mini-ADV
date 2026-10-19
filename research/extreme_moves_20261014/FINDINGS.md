# Extreme index moves: book profits or hold? (14 Oct 2026)

**Spot data:** NIFTY, BANKNIFTY, SENSEX and MIDCPNIFTY, 1-min, 2021 → Oct 2026. ATR is the 14 sessions before the day.

**Option data:** real 1-min premiums from TradeBot (NIFTY Apr–Aug 2025, SENSEX Apr 2025 – Feb 2026).
- Each option is the ATM contract of the nearest expiry with at least one more session; expiry-day decay is excluded.
- It is priced at the minute the index first reaches k × ATR from the previous close.

## Spot (`spot_events.py`, `out/spot_events.txt`)
| Event | What follows (signed in the move's direction) |
|---|---|
| Index first ≥ 2 ATR below the previous close | −15 to −18 bps in the next 30–60 min (a bounce). D+1 −68 to −81 bps; only 27–33% fall further. |
| Day closes ≥ 1.5 / ≥ 2 ATR down | D+1 −45 / −94 bps (60% / 73% bounce) |
| 3-session fall ≥ 3 ATR | D+1 −67 bps (71% bounce) |
| Index 1–1.5 ATR up intraday | Continues: D+1 +10–21 bps, D+3 +35–49 bps, 58–63% continue |
| Day closes ≥ 2 ATR up | D+1 −96 bps (gives back), then D+3 +50 bps |
| 3-session rise ≥ 3 ATR | Continues: D+3 +75 bps, 77% |

**Asymmetry:** in this mostly rising sample, down extremes revert and up extremes extend. Results by year are not
uniform.

## Real options (`option_events.py`, `out/option_events.txt`)
133 events, mostly at 1 ATR; 31 at ≥ 1.5 ATR. Each cell is the change of premium from the event if HELD.

| Option on the move's side | +60 min | Same-day close | D+1 | D+3 |
|---|---|---|---|---|
| PE after a 1-ATR down move (n 54) | mean −9.6%, 27% gained | −13.1%, 33% | median −35%, 38% | median −80%, 19% |
| CE after a 1-ATR up move (n 51) | +2.6%, 44% | median −13.9%, 26% | median −31%, 32% | mean +12% but median −46% |
| CE after a ≥ 1.5 ATR up move (n 10) | +24%, 50% | mean +49% / median −9% | −37% | – |
| PE after a ≥ 2 ATR down move (n 4) | −18% to +29% | −8% to −18% | about −80% | about −100% |

- **Consistent by index:** NIFTY and SENSEX agree. For example, PE held to the close: NIFTY −18%, SENSEX −10%. CE held
  to D+1: NIFTY median −14%, SENSEX median −39%.
- **The fade side:** a CE bought against a 1-ATR down move made +6.6% in 60 min (69% gained), a short-lived bounce.

## Conclusions
- **PE positions:** when the index is ≥ 1 ATR down on the day, book at the extreme rather than hold. The premium is
  more likely lower 30–60 min later, lower at the close, and much lower the next day. Never carry puts overnight after
  a ≥ 1.5 ATR down day.
- **CE positions:** spot tends to extend, but the premium does not keep up (IV falls on rallies, plus theta). Book by
  the close; the median position loses overnight. After a ≥ 2 ATR up day, the next day gives back. A small runner
  keeps the right tail (D+3 mean +12%), but the median is −46%.
- **This supports a "book on extreme" overlay** for index option positions, especially puts.
- **Caveats:**
  - The ≥ 1.5 ATR option sample is small (n ≤ 15).
  - The option data is 2025–26 only.
  - The four indices move together, so events are not independent.
  - Premiums are last-trade marks.
