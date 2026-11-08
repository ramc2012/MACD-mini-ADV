# MACD Mini for iOS

A read-only SwiftUI client for the MACD mini paper-trading engine (stack `macd-mini-parallel`, API on :8201).
It never places, changes or closes trades.

| Tab | Engine endpoint | Shows |
|---|---|---|
| Lanes | `/api/scoreboard`, `/api/system/health` | every paper book: equity, return, booked/open P&L, sparkline; detail with equity curve, net by week, closed-trade stats, exits by reason; engine/feed/Fyers-token status |
| MACD book | `/api/portfolio` | open positions, sortable by P&L / return / newest, searchable |
| Signals | `/api/signals` | latest 200 signals grouped by IST day, Buy/Sell filter |
| Sectors | `/api/sector-coil` | the Sector coil drawn natively (same geometry as the web view), start / turns / own-vs-NIFTY controls, tap a sector for each turn's value |
| Settings | — | engine address and API token |

Screens refresh every 20–60 s while open and on pull-to-refresh. Times are shown in IST.

## Run it

```bash
open ios/MACDMini.xcodeproj
```

Pick an iPhone simulator and Run. No signing team is needed for the simulator. On first launch, open Settings and
paste the engine's `MACD_API_TOKEN` (from the project's `.env`); it is stored in the device Keychain. To copy it
without showing it: `grep '^MACD_API_TOKEN=' .env | cut -d= -f2- | pbcopy`, then paste in the simulator.

The simulator reaches the engine at `http://localhost:8201`. A physical iPhone needs the Mac's LAN address, and
compose.yaml publishes the engine on `127.0.0.1` only, so that would need a deliberate port change (and the token
then travels over plain HTTP on the LAN).

## Layout

- `MACDMini.xcodeproj`: one app target; the `MACDMini/` folder is a synchronized group, so new `.swift` files are
  picked up without editing the project.
- `MACDMini/Engine.swift`: API client (`X-Macd-Token` header), Keychain, app state, polling.
- `MACDMini/Models.swift`: JSON shapes (snake_case decoded to camelCase; fields optional).
- `MACDMini/Format.swift`: ₹ lakh/crore, signed %, IST dates.
- `Support/Info.plist`: local-networking ATS exception and local-network prompt text.

Requires Xcode 16+ (built with Xcode 27.2), iOS 18+.
