export function orderedEquityPoints(rows: { time: number; value: number }[]) {
  const byTime = new Map<number, number>();
  for (const row of rows) {
    if (Number.isFinite(row.time) && Number.isFinite(row.value)) byTime.set(row.time, row.value);
  }
  return [...byTime].sort((a, b) => a[0] - b[0]).map(([time, value]) => ({time, value}));
}

export function realizedEquityPoints(trades: { exit_time: string; pnl: number }[]) {
  let cumulative = 0;
  const rows = trades.map(row => ({time: Math.floor(Date.parse(row.exit_time) / 1000), pnl: row.pnl}))
    .filter(row => Number.isFinite(row.time) && Number.isFinite(row.pnl))
    .sort((a, b) => a.time - b.time);
  return orderedEquityPoints(rows.map(row => ({time: row.time, value: cumulative += row.pnl})));
}
