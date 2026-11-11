/** Open positions with the most recently entered first: the default order on every positions page (owner, 11 Nov
 *  2026). Rows without a readable entry time go last, in their original order. */
export function newestFirst<T>(
  rows: readonly T[],
  openedAt: (row: T) => string | null | undefined = (row) => (row as { opened_at?: string | null }).opened_at,
): T[] {
  const time = (row: T) => {
    const value = Date.parse(openedAt(row) ?? "");
    return Number.isFinite(value) ? value : Number.NEGATIVE_INFINITY;
  };
  return [...rows].sort((a, b) => {
    const ta = time(a), tb = time(b);
    return ta === tb ? 0 : tb > ta ? 1 : -1;
  });
}
