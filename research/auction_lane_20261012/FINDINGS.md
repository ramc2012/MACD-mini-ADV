# Auction (MP / order-flow) lane review: 12 Oct 2026

## Data
The lane has run on only 4 sessions: 29 Sep, 30 Sep, 1 Oct and 9 Oct (the app was down 5–8 Oct).
- **Book:** 48 round trips, −Rs 2.19 L on Rs 10 L.
- **Trade cap:** the 12-entries-a-day cap was hit every day.
- **Signals:** 691 of the 700 logged signals were scored on their real option 1-minute bars. Costs are 25 bps
  slippage each side plus Rs 40.

## Findings (underpowered: 4 days, mostly falling)
- **No edge in the signals.** Mean return per signal by exit rule:
  - lane exits (−25% stop, 20% trail from +20%): −0.3%;
  - hold to 15:20: −3.1%;
  - wider stops: −1.2%.
- **The opposite option did better at the same moment:** +2.5% under lane exits, +4.7% held to the close.
  Breakouts mostly reversed intraday. Call breakouts were worst:
  - opening-range breakout CE: −4.7%;
  - initial-balance extension CE: −9% (n=11);
  - value-area breakout CE: −13% (n=6).
- **Time of day.** The 09:xx signals were worst (−5.7%). Because the 12-a-day budget is first-come, they were most of
  what the book bought. The simulated current book on these signals is −Rs 4.0 L.
- **Holding period.** A 60-minute exit was the best rule tested: +1.2% a signal, 48% win.
- **Index vs stock signals:**
  - index signals: +5.6% under lane exits, but −6.5% held to 15:20 (weekly decay);
  - stock signals: −2.2% under lane exits.

## Changes made (12 Oct, before the open)
- `MIN_BRACKETS_BEFORE_ENTRY` 2 → 3. Entries now wait for the initial balance to complete (from 10:15).
- `MAX_HOLD_SECONDS` = 3600, with exit reason `MP_TIME`.
- **Book simulation on the same signals:** current rules −Rs 4.0 L; the tuned variants −0.2 L to +0.5 L. This is
  in-sample over 4 days, so not proven.

## Not changed (evidence too thin; revisit with more sessions)
- **Dropping or flipping the call breakout setups.** This would fade the signal and take the opposite side.
- **Index-only trading.** The +Rs 2.2 L result came mostly from one day.
- **Raising the 12-a-day cap.**
