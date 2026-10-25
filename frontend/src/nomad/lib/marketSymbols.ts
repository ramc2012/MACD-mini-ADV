export const MARKET_INDEX_SYMBOLS = [
  "NSE:NIFTY50-INDEX",
  "NSE:BANKNIFTY-INDEX",
  "NSE:FINNIFTY-INDEX",
  "NSE:MIDCPNIFTY-INDEX",
  "BSE:SENSEX-INDEX",
] as const;

export const SECTOR_INDEX_SYMBOLS = [
  "NSE:NIFTYBANK-INDEX",
  "NSE:NIFTYIT-INDEX",
  "NSE:NIFTYAUTO-INDEX",
  "NSE:NIFTYPHARMA-INDEX",
  "NSE:NIFTYFMCG-INDEX",
  "NSE:NIFTYMETAL-INDEX",
  "NSE:NIFTYENERGY-INDEX",
  "NSE:NIFTYREALTY-INDEX",
] as const;

export type MarketIndexSymbol = (typeof MARKET_INDEX_SYMBOLS)[number];

export const MARKET_INDEX_LABELS: Record<MarketIndexSymbol, string> = {
  "NSE:NIFTY50-INDEX": "NIFTY",
  "NSE:BANKNIFTY-INDEX": "BANKNIFTY",
  "NSE:FINNIFTY-INDEX": "FINNIFTY",
  "NSE:MIDCPNIFTY-INDEX": "MIDCPNIFTY",
  "BSE:SENSEX-INDEX": "SENSEX",
};

export const MARKET_INDEX_PRICE_BANDS: Record<MarketIndexSymbol, readonly [number, number]> = {
  "NSE:NIFTY50-INDEX": [10_000, 50_000],
  "NSE:BANKNIFTY-INDEX": [20_000, 100_000],
  "NSE:FINNIFTY-INDEX": [10_000, 60_000],
  "NSE:MIDCPNIFTY-INDEX": [5_000, 40_000],
  "BSE:SENSEX-INDEX": [30_000, 150_000],
};

export const MARKET_SYMBOL_LABELS: Record<string, string> = {
  ...MARKET_INDEX_LABELS,
  "NSE:NIFTYBANK-INDEX": "NIFTY BANK",
  "NSE:NIFTYIT-INDEX": "NIFTY IT",
  "NSE:NIFTYAUTO-INDEX": "NIFTY AUTO",
  "NSE:NIFTYPHARMA-INDEX": "NIFTY PHARMA",
  "NSE:NIFTYFMCG-INDEX": "NIFTY FMCG",
  "NSE:NIFTYMETAL-INDEX": "NIFTY METAL",
  "NSE:NIFTYENERGY-INDEX": "NIFTY ENERGY",
  "NSE:NIFTYREALTY-INDEX": "NIFTY REALTY",
};

export function getMarketIndexLabel(symbol: string): string {
  return MARKET_SYMBOL_LABELS[symbol] ?? symbol;
}

// ── Lane-scoped terminal helpers ────────────────────────────────────────────
// underlying label ("NIFTY") → live-tape index symbol ("NSE:NIFTY50-INDEX").
export const UNDERLYING_TO_INDEX_SYMBOL: Record<string, MarketIndexSymbol> = Object.fromEntries(
  (Object.entries(MARKET_INDEX_LABELS) as [MarketIndexSymbol, string][]).map(
    ([sym, label]) => [label.toUpperCase(), sym],
  ),
) as Record<string, MarketIndexSymbol>;

export function underlyingToTapeSymbol(underlying?: string | null): string | null {
  if (!underlying) return null;
  return UNDERLYING_TO_INDEX_SYMBOL[String(underlying).toUpperCase().trim()] ?? null;
}

/** A string is a broker/tape symbol when it carries a namespace separator
 *  ("NSE:...-INDEX", "NSE_FO|..."). trading_symbol ("NIFTY 24450 CE") is not. */
function looksLikeTapeSymbol(value: unknown): value is string {
  return typeof value === "string" && (value.includes(":") || value.includes("|"));
}

type SymbolRow = Record<string, unknown>;
const _SYMBOL_FIELDS = ["tape_symbol", "live_symbol", "instrument_key", "symbol"] as const;

/**
 * The tape symbol for a row's OWN instrument only — a namespaced broker key on
 * the row (an option/future leg's premium symbol). Returns null when the row has
 * no streamable leg key. Use this for OPTION-LEG cells: never fall back to the
 * underlying index (that would show the index spot as the option's price).
 * `trading_symbol` ("NIFTY 24450 CE") is intentionally NOT a tape symbol.
 */
export function legTapeSymbol(row: unknown): string | null {
  if (!row || typeof row !== "object") return null;
  for (const key of _SYMBOL_FIELDS) {
    const v = (row as SymbolRow)[key];
    if (looksLikeTapeSymbol(v)) return v;
  }
  return null;
}

/**
 * The tape symbol for a row, preferring its own leg key and falling back to the
 * underlying's index tape symbol. Use this for UNDERLYING/spot cells (e.g. a
 * universe watchlist showing index spot), NOT for option-leg premium cells.
 */
export function rowTapeSymbol(row: unknown): string | null {
  return legTapeSymbol(row) ?? underlyingToTapeSymbol((row as SymbolRow)?.underlying as string | undefined);
}

/**
 * Derive the live-tape symbols a lane cares about from its watchlist +
 * open-position rows: each row's `underlying` maps to its index tape symbol,
 * and any direct broker-format symbol field (option legs / futures) is kept.
 * Robust to the differing row schemas across desks. Returns a de-duped,
 * index-first ordered list.
 */
export function laneTapeSymbols(...rowGroups: (readonly unknown[] | undefined | null)[]): string[] {
  const out = new Set<string>();
  for (const group of rowGroups) {
    for (const row of group ?? []) {
      if (!row || typeof row !== "object") continue;
      const idx = underlyingToTapeSymbol((row as SymbolRow).underlying as string | undefined);
      if (idx) out.add(idx);
      for (const key of _SYMBOL_FIELDS) {
        const v = (row as SymbolRow)[key];
        if (looksLikeTapeSymbol(v)) out.add(v);
      }
    }
  }
  // Indices first (they stream intraday), then legs/futures, both alphabetized.
  const all = Array.from(out);
  const isIndex = (s: string) => s.endsWith("-INDEX");
  return all.sort((a, b) => {
    const ai = isIndex(a) ? 0 : 1;
    const bi = isIndex(b) ? 0 : 1;
    return ai !== bi ? ai - bi : a.localeCompare(b);
  });
}
