# MACD Mini for iOS

A SwiftUI client for the MACD mini paper-trading engine (stack `macd-mini-parallel`, API on :8201). It reads
every lane and the market breadth views, and handles broker logins for market data. It never places, changes or closes
trades.

| Tab | Endpoints | Shows |
|---|---|---|
| Lanes | `/api/scoreboard`, `/api/system/health` | every paper book: equity, return, booked/open P&L, sparkline; engine/feed/Fyers-token status |
| Lane page | per lane, below | stats, **open positions** (top 6, then all: sortable, searchable), **recent exits**, equity curve, net by week, closed-trade stats, exits by reason |
| Signals | `/api/signals` | latest 200 signals grouped by IST day, Buy/Sell filter |
| Breadth | `/api/day-highs`, `/api/sector-wheel`, `/api/sector-coil`, `/api/heatmap/atm`, `/api/rrg` | Day highs (spots, ATM CE/PE), Sector wheel (1D–60D, own or vs NIFTY 50, month-start leaders), Sector coil, ATM heat map, RRG with tails and per-sector stocks |
| Brokers | engine `/api/settings`, `/api/auth/fyers/*`; console `/api/logins*`, `/api/brokers/upstox/credentials` | Fyers status, refresh (saved refresh token + PIN), in-app login, paste box, app details; Upstox status, login, paste box, app details |
| Settings | — | engine address, API token, platform console address |

Where each lane's book comes from:

| Lane | Endpoint |
|---|---|
| MACD | `/api/portfolio` (positions + desk exits) |
| Smart 1L / 50L, Spot MACD | `/api/lanes/{name}/snapshot` |
| Blast | `/api/blast/snapshot` |
| Agentic | `/api/agentic` |
| Auction / MP | `/api/mp/book` |
| Vanguard, CBE | `/api/paper-lanes/{lane}` (paise fields; open P&L marked at the last bid after the entry fee; ~1–2 MB, read every 60 s) |

Screens refresh every 20–120 s while open and on pull-to-refresh. Times are shown in IST.

### Broker logins

- **Fyers** goes through the engine. *Refresh token now* calls `/api/auth/fyers/refresh` (needs the PIN saved under App
  details). *Log in to Fyers* gets the login address from the engine, opens it only if it is an https `fyers.in` page,
  in a private web view (no cookies kept), and catches the redirect to the app's redirect URL before it loads; its
  `auth_code` goes to `/api/auth/fyers/exchange`. The paste box sends a redirect address, auth code or access token to
  `/api/auth/fyers/access-token`.
- **Upstox** goes through the market-intelligence console (default `http://localhost:8410`, set in Settings): login
  address from `/api/logins/upstox/url` (https `upstox.com` only), redirect caught the same way, `code` sent to
  `/api/logins/upstox`.
- PINs, secrets and pasted tokens are sent once to that service, cleared from the screen, and never stored or logged
  by the app.

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

## Testing on several simulators

Tested 8 Nov 2026 on the iOS 27.2 simulator: iPhone 16, 16e, 16 Plus, 16 Pro, 16 Pro Max, 17, 17e, 17 Pro,
17 Pro Max, Air, 18 Pro, 18 Pro Max (all tabs), and the iPhone Duo's outer screen (the system shows the tab bar as a
side rail there; the unfolded inner screen was not reachable from `simctl`).

Debug simulator builds read launch variables, so a sweep doesn't need typing into Settings. They are compiled out of
release and device builds:

```bash
SIMCTL_CHILD_MACD_UITEST_BASE=http://localhost:8201 \
SIMCTL_CHILD_MACD_UITEST_TOKEN="$(grep '^MACD_API_TOKEN=' .env | cut -d= -f2-)" \
SIMCTL_CHILD_MACD_UITEST_TAB=sectors \
xcrun simctl launch booted local.macdmini.client
```

`MACD_UITEST_TAB` is one of `lanes`, `signals`, `breadth`, `brokers`, `settings`; `MACD_UITEST_LANE=<lane>` opens a
lane page (e.g. `vanguard`); `MACD_UITEST_VIEW` opens a breadth page (`highs`, `wheel`, `coil`, `atm`, `rrg`);
`MACD_UITEST_CONSOLE` sets the console address. The base, console and token are saved as if typed in Settings. Debug
simulator builds also accept a `localhost` login page, so the login flow can be tested against a mock; release and
device builds accept only the brokers' https pages.

Lane books, Breadth and Brokers were tested 8 Nov 2026 on iPhone 16 (all lanes, all breadth pages, Fyers and Upstox
login through a mock engine and console, including a login that returns no code), and spot-checked on iPhone 16e and
18 Pro Max. Screenshot with `xcrun simctl io booted screenshot out.png` (a foldable needs `--display=N`;
`xcrun simctl io <udid> enumerate` lists them).

If every `simctl` command hangs after an Xcode update, Xcode's simulator components are older than it expects;
run `sudo xcodebuild -runFirstLaunch` once.

## Layout

- `MACDMini.xcodeproj`: one app target; the `MACDMini/` folder is a synchronized group, so new `.swift` files are
  picked up without editing the project.
- `MACDMini/Engine.swift`: API client (GET/POST; `X-Macd-Token` for the engine, none for the console), Keychain, app state, polling.
- `MACDMini/LaneBooks.swift`: each lane's book endpoint, normalised to one positions/exits shape.
- `MACDMini/BreadthModels.swift`, `BreadthView.swift`, `SectorCoilView.swift`: the Breadth tab.
- `MACDMini/BrokersView.swift`: broker status, logins (private `WKWebView`), app details.
- `MACDMini/Models.swift`: JSON shapes (snake_case decoded to camelCase; fields optional).
- `MACDMini/Format.swift`: ₹ lakh/crore, signed %, IST dates.
- `Support/Info.plist`: local-networking ATS exception and local-network prompt text.

Requires Xcode 16+ (built with Xcode 27.2), iOS 18+.
