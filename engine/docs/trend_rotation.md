# Trend rotation

Market breadth → Trend rotation (`#/MACD/MARKET/TREND`) is a separate absolute-price view. Existing relative RRG remains available.

- ATM calls and ATM puts: **Trending up** or **Fading**, using each exact contract's own premium. A rising put is Trending up even when spot falls.
- Spot: **Trending up**, **Trending down** or **Fading**.
- Rising trend requires EMA5 above EMA20 and a positive three-bar EMA5 slope. Spot's down state requires both conditions reversed. All other valid patterns are fading, including flat premiums and rebounds not yet above the longer average.
- Horizontal position is `(EMA5 − EMA20) / mean(abs(close changes), 14)`. Vertical position is `(EMA5[t] − EMA5[t−3]) / (3 × scale)`. A small price-relative scale floor avoids division by zero. The origin defines the state boundaries; the two axes have separate display ranges.
- Only completed bars from the instrument's existing session calendar are used. Latest expected completed bar must exist; at least 21 bars are required. Missing/stale history is listed separately and never assigned a price state.
- 30m, 1h and daily intervals; last 2–8 tail points; state counts, search, sector filter, exact-contract details and last stored price timestamp. Daily spot uses completed `daily_closes` when available. Option tails always use a single exact contract, never a rolled ATM price series.
- Read-only API `/api/trend-rotation?kind=CE&timeframe_seconds=1800`. Other kinds: PE, SPOT; intervals: 1800, 3600, 86400. It uses stored data and a 60-second computation cache, without new broker calls or execution hooks.

This is a descriptive state view, not a calibrated trading signal, a forecast, or permission to place orders. Fading intentionally combines all option patterns that do not satisfy the rising-trend definition. No lane rules or capital settings are changed.
