// Click-to-sort for rendered tables: keys come from the cell text the owner sees (₹, %, signs, IST timestamps).
export type SortKey = {kind: 0 | 1 | 2 | 3; num: number; text: string};   // 0 number, 1 timestamp, 2 text, 3 blank

const BLANK = /^(—|-|–|n\/a|na|unavailable|none)?$/i;

export function sortKey(raw: string): SortKey {
  const text = raw.replace(/\s+/g, ' ').trim();
  if (BLANK.test(text)) return {kind: 3, num: 0, text: ''};
  if (/^\d{4}-\d{2}-\d{2}/.test(text)) return {kind: 1, num: 0, text};
  const m = text.replace(/[₹,]/g, '').replace(/−/g, '-').match(/^[#(]?\s*([+-]?\s*\d+(?:\.\d+)?)/);
  if (m) {
    const n = Number(m[1].replace(/\s/g, ''));
    if (Number.isFinite(n)) return {kind: 0, num: /^\(/.test(text) ? -Math.abs(n) : n, text};
  }
  return {kind: 2, num: 0, text: text.toLowerCase()};
}

// Blanks always sort last, whichever direction.
export function compareKeys(a: SortKey, b: SortKey, dir: 1 | -1): number {
  if (a.kind === 3 || b.kind === 3) return a.kind === b.kind ? 0 : a.kind === 3 ? 1 : -1;
  if (a.kind !== b.kind) return (a.kind - b.kind) * dir;
  if (a.kind === 0 && a.num !== b.num) return (a.num - b.num) * dir;
  return a.text.localeCompare(b.text, 'en', {numeric: true}) * dir;
}
