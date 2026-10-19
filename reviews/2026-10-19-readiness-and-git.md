# MACD app source and pre-open readiness — 19 October 2026

Verified at approximately 07:55 IST, before the regular session. Scope is the
maintained MACD-mini-ADV stack: strategy engine, Smart and Blast paper lanes,
Auction Desk, frontend, Go gateway, Rust analytics and retained US source.
This is not an audit of the separate TradeBot deployment or retired MACD-mini.

## Source and deployment

The working folder had no `.git` metadata. The existing GitHub repository
`ramc2012/MACD-mini-ADV`, on `main` at `cc30227`, was cloned without checking
out files. Its history/index were reattached to this source folder without
overwriting local files. All outstanding app source, tests, documentation and
research code/findings were prepared against that history. No force reset or
force push was used.

Credentials, `.env`, local Compose overrides, runtime state, database copies,
market-data dumps, generated results and copied dependencies are ignored.
The IPv6 broker connection recipe is documented in
`engine/docs/broker_ipv6.md`; the live machine-local override remains in place.

Both running Python services match all 76 local source modules. Fresh builds
of the frontend, gateway and analytics produce identical served assets or
binaries to the running services. The engine image was rebuilt and both
Python services recreated to deploy the nightly coverage correction below.

Validation: **1,007 engine tests and 88 frontend tests pass**. Frontend,
gateway, analytics and engine production builds pass. The gateway/Rust build
checks reused their cached, unchanged build stages. The staged source passes
whitespace and credential-pattern checks.

## Correction made during this audit

Nightly completeness used a shared 09:15–15:30 clock. That demanded minutes
after cash continuous trading had ended and omitted the derivative session's
tail under the app's configured session regime. It now uses the instrument's
own date-specific continuous-session window and reports each symbol's
expected count. Missing bars and read failures still block processing.
Regression coverage proves that a complete index session cannot conceal a
missing derivative closing minute.

## Pre-open operating checks

- Fyers connected; socket alive; token expires **20 October, 06:00 IST**.
- **684/684** required indicators available; zero history-request errors,
  zero lane errors and no unresolved expired holdings.
- MACD, Smart and Blast retain their existing automatic paper preferences.
  Execution is **paper**, with **live orders locked**.
- Strategy and desk NATS connections are live, with zero lost/undecodable
  desk messages and zero dropped strategy publications. A retained desk
  `unexpected EOF` message records the restart; the current connection and
  desk health are good.
- Blast has **472/472** histories warmed. Its VIX policy is awaiting a fresh
  session quote. Auction Desk correctly remains blocked by the closed
  session and insufficient classified prints.

Six paper databases pass `quick_check(1)`. Every saved fill links to a FILLED
order; no filled order is missing a fill. The main book has **4,513 order
attempts = 1,328 FILLED + 3,185 REJECTED**, and **1,328 fills = 572 BUY + 756
SELL**. The other five books also reconcile without missing links.

Online backups are retained at
`/app/data/backups/pre-readiness-20261019/` in the engine data volume. Full
orders, trades, closed positions, capital changes, Smart position state and
excursion/risk values match before/after fingerprints (routine `updated_at`
stamps excluded). Open positions, cash and P&L are unchanged in every lane.
Settings and credentials are byte-identical. Frozen symbol groups are
unchanged; startup refreshed the contract snapshot's `selected_at` stamp.

| Book | Open positions | Cash available |
|---|---:|---:|
| MACD | 32 | ₹71,24,915.94 |
| Blast | 8 | ₹49,99,59,222.25 |
| Smart ₹1L | 7 | ₹39,208.55 |
| Smart ₹50L | 38 | ₹12,173.91 |
| Auction Desk | 0 | ₹7,25,059.71 |

Smart ₹50L therefore has limited capacity for new entries until cash is
released by exits. No funding or sizing settings were changed.

## History coverage and remaining limits

Friday 16 October was audited against the deployed per-instrument session
rules. Retrying 18 incomplete spot histories recovered **77** missing
continuous-session minutes, giving **214/214 complete spots** and **77,100
distinct minute rows**. All audited rows have valid OHLCV and aligned minute
timestamps.

| Group | Symbols | Complete sessions | Partial sessions | No Friday rows |
|---|---:|---:|---:|---:|
| Selected/retained options | 462 | 297 | 164 | 1 |
| Analysis options | 883 | 330 | 319 | 234 |
| Held exact contracts, across books | 69 | 38 | 30 | 1 |

Groups overlap for held contracts. Newly selected or illiquid options may
have sparse or absent provider history; no candles were fabricated.
`NSE:PIIND26OCT2100CE` remains held without Friday history. A direct Fyers
retry returned zero candles; its pre-open quote had zero volume and no
bid/ask. Old marks are not evidence of an executable exit.

In the selected and analysis universe, **298,247** Friday option rows lack
expiry metadata. No bulk metadata repair was attempted in this run; those
rows limit historical research certification. Before this run's restart,
the earlier 90-day background pass had processed 400/417 contracts. The
remaining older-history coverage is not certified by completed strategy
warm-up, and the restarted process schedules no new-contract pass for the
already frozen selection.

The rendered Status page confirms connected Fyers, 684/684 indicators,
exposure, Greeks and reconciled ledger counts. Portfolio remains positions
only. Existing positions and the live-order lock were preserved.

**Assessment:** prepared for the regular-session paper checks, with the
history/liquidity exceptions above. Pre-open evidence cannot establish
fresh in-session execution. After 09:15 IST, require current exchange and
receipt timestamps, advancing candle counters, healthy desk delivery and
sufficient classified prints before calling every lane ready. A successful
pre-open connection is not a claim of profitability or live-trading approval.

Detailed local evidence is in ignored `runtime/readiness-2026-10-19/`,
including before/after audits, fingerprints, source/binary parity, provider
retries and test logs.
