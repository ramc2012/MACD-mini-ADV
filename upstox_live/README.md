# Upstox live lane

Follows the MACD lane's signals with **real Upstox orders**, sized from the account's **available margin** (read every
15 s, so a top-up is used within seconds). Optional cap: `UPSTOX_LIVE_CAPITAL` in `.env` (> 0 limits the lane to cap +
booked P&L − deployed; default 0 = no cap). Return % is shown on the ₹25,000 start. Container `upstox-live`; page: web
app → **Upstox live** (`/api/upstox-live/snapshot`).

- **Entry:** every new MACD option BUY signal whose one-lot cost (signal price + 2 %, tick-rounded LIMIT) plus ₹60 for
  charges fits the free fund = Upstox available margin − entries sent since that read. No entries when the funds read is
  older than 90 s or the order API is blocked. Product D, DAY, tag `MACDLIVE`. Unfilled after 90 s → cancelled.
- **Side filter and priority** (Settings → Upstox live, saved in `runtime/upstox_live/settings.json`): *Calls and
  puts* / *Calls only* / *Puts only*. Signals of the other side are ignored and not shown. Weekly-index signals
  (`WEEKLY_INDEX_ROOTS`, default NIFTY, SENSEX) come first: other signals wait `PREFER_WAIT_SECONDS` (10 s) so a
  weekly-index signal arriving meanwhile is funded first.
- **Exit:** LIMIT SELL of the whole quantity when the MACD paper lane closes the contract (or never takes it within
  10 min): 3 % under the last price, then 6 %, then 10 % if unfilled after 60 s.
- **Limits:** one position per contract, at most 30 orders a day, never sells more than it holds, ignores signals older
  than 120 s or from before the lane started.

## Arming (owner only)

The lane starts as a **dry run**: it journals what it would order and sends nothing. Real orders go out only while
`runtime/upstox_live/ARMED` contains `yes`. **Arming is one-time:** it stays on across days, restarts and Upstox
re-logins until Kill (an expired daily token only pauses entries until the next login):

```bash
echo yes > runtime/upstox_live/ARMED     # arm: real orders from the next signal
# or Settings → Upstox live: Arm real orders / Kill (disarm) / Exit all lane positions
rm runtime/upstox_live/ARMED             # disarm: back to dry run within 5 s
docker compose stop upstox-live          # stop the lane entirely
```

Upstox's order and account APIs answer `UDAPI1221` unless the request comes from the static IP registered on the
Upstox API app; register this Mac's public IP there first. Test offline (no network): `python3 -I upstox_live/test_lane_offline.py`.
This lane uses the platform's daily OAuth token (`secrets/upstox-trade.token`) for its account reads and order calls.
When the token is missing, the lane does not fall back to the analytics token. The platform continues to use its
own analytics token for market data. Neither token is logged or served.
