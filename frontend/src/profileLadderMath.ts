export type LadderLevel = { price: number; tpo: number; letters: string; volume: number };
export type LadderRow = LadderLevel & { empty: boolean };

const BRACKETS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz";

export function bracketLetters(levels: LadderLevel[], brackets: number): string[] {
  const observed = new Set<string>();
  for (const level of levels) {
    for (const letter of level.letters || "") {
      if (BRACKETS.includes(letter)) observed.add(letter);
    }
  }
  // A late-joined profile may only contain K. Empty A–J columns waste space
  // and imply that those brackets were captured. Keep the real letter/time.
  return observed.size ? [...observed].sort((a, b) => BRACKETS.indexOf(a) - BRACKETS.indexOf(b))
    : BRACKETS.slice(0, Math.max(0, Math.min(BRACKETS.length, brackets))).split("");
}

export function bracketIndex(letter: string): number { return BRACKETS.indexOf(letter); }

export function bracketTime(index: number): string {
  const minute = 9 * 60 + 15 + index * 30;
  const end = minute + 30;
  const clock = (m: number) => `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
  return `${clock(minute)}–${clock(end)} IST`;
}

export function priceDigits(tickSize: number | null | undefined, levels: LadderLevel[]): number {
  const tick = tickSize && Number.isFinite(tickSize) && tickSize > 0 ? tickSize : null;
  if (tick !== null) {
    for (let digits = 0; digits <= 6; digits++) {
      if (Math.abs(tick * 10 ** digits - Math.round(tick * 10 ** digits)) < 1e-6) return digits;
    }
  }
  return levels.some((level) => Math.abs(level.price * 100 - Math.round(level.price * 100)) > 1e-5) ? 3
    : levels.some((level) => Math.abs(level.price * 10 - Math.round(level.price * 10)) > 1e-5) ? 2 : 1;
}

/**
 * A complete payload can be shown on a true tick ladder, with blank rows where
 * no trade occurred. For a sampled payload gaps are ambiguous, so only its
 * published rows are drawn. The hard cap protects the UI from a malformed tick.
 */
export function ladderRows(levels: LadderLevel[], tickSize: number | null | undefined, complete: boolean): LadderRow[] {
  const sorted = levels.filter((row) => Number.isFinite(row.price))
    .sort((a, b) => b.price - a.price);
  if (!sorted.length) return [];
  const unique = sorted.filter((row, i) => i === 0 || Math.abs(row.price - sorted[i - 1].price) > 1e-8);
  if (!complete || !tickSize || !Number.isFinite(tickSize) || tickSize <= 0) {
    return unique.map((row) => ({ ...row, empty: false }));
  }
  const top = Math.round(unique[0].price / tickSize);
  const bottom = Math.round(unique[unique.length - 1].price / tickSize);
  if (top - bottom > 12_000 || top < bottom) return unique.map((row) => ({ ...row, empty: false }));
  const byTick = new Map(unique.map((row) => [Math.round(row.price / tickSize), row]));
  const rows: LadderRow[] = [];
  for (let n = top; n >= bottom; n--) {
    const level = byTick.get(n);
    rows.push(level ? { ...level, empty: false } : {
      price: Math.round(n * tickSize * 1e6) / 1e6,
      tpo: 0, letters: "", volume: 0, empty: true,
    });
  }
  return rows;
}

export function nearestPriceIndex(rows: LadderRow[], price: number | null | undefined): number {
  if (!rows.length || price == null || !Number.isFinite(price)) return 0;
  let lo = 0;
  let hi = rows.length - 1;
  while (lo < hi) {
    const mid = Math.floor((lo + hi) / 2);
    if (rows[mid].price > price) lo = mid + 1;
    else hi = mid;
  }
  if (lo > 0 && Math.abs(rows[lo - 1].price - price) < Math.abs(rows[lo].price - price)) return lo - 1;
  return lo;
}
