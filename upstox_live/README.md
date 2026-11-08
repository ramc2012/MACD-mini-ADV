# Upstox live lane

Follows the MACD lane's signals with **real Upstox orders** inside a fixed fund (`UPSTOX_LIVE_CAPITAL`, default
₹25,000). Container `upstox-live` in this stack; page: web app → **Upstox live** (`/api/upstox-live/snapshot`).

- **Entry:** the first new MACD BUY signal whose one-lot cost (signal price + 2 %, tick-rounded LIMIT) fits the free fund
  (fund + booked P&L − deployed, never above Upstox's available margin). Product D, DAY, tag `MACDLIVE`. Unfilled
  after 90 s → cancelled.
- **Exit:** LIMIT SELL of the whole quantity when the MACD paper lane closes the contract (or never takes it within
  10 min): 3 % under the last price, then 6 %, then 10 % if unfilled after 60 s.
- **Limits:** one position per contract, at most 30 orders a day, never sells more than it holds, ignores signals older
  than 120 s or from before the lane started.

## Arming (owner only)

The lane starts as a **dry run**: it journals what it would order and sends nothing. Real orders go out only while
`runtime/upstox_live/ARMED` contains `yes`:

```bash
echo yes > runtime/upstox_live/ARMED     # arm: real orders from the next signal
# or Settings → Upstox live: Arm real orders / Kill (disarm) / Exit all lane positions
rm runtime/upstox_live/ARMED             # disarm: back to dry run within 5 s
docker compose stop upstox-live          # stop the lane entirely
```

Upstox's order and account APIs answer `UDAPI1221` unless the request comes from the static IP registered on the
Upstox API app; register this Mac's public IP there first. Test offline (no network): `python3 -I upstox_live/test_lane_offline.py`.
The Upstox token is read from the platform's `secrets/upstox.token` and is never logged or served.
