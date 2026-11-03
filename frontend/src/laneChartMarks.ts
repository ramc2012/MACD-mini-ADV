import type { ChartLevel, ChartMarker, Trade } from "./types";

// What a lane's position chart draws on top of the bars: every fill of that symbol (or of one position, between
// its entry and exit) and the position's working prices. Pure, so the tests can pin it without a chart.

export type LevelSource = {
  average_price?: number | null; hard_stop?: number | null; trailing_stop?: number | null; target_price?: number | null;
};

const epoch = (iso: string) => Math.floor(Date.parse(iso) / 1000);

/** Markers for one symbol's fills, oldest first. Within [from, until] when given (one position's life, inclusive
 * of a few seconds' slack for fill stamps); with `from` and no `until` (null or omitted) the window closes at the
 * fill that takes the position flat, so a later re-entry is not drawn on an earlier position. The first buy after
 * flat is ENTRY, later buys ADD; a sell is EXIT, red when it sold below the running average cost. */
export function fillMarkers(trades: readonly Trade[], symbol: string, from?: string, until?: string | null): ChartMarker[] {
  const start = from ? epoch(from) - 5 : Number.NEGATIVE_INFINITY;
  const end = until ? epoch(until) + 5 : Number.POSITIVE_INFINITY;
  const rows = trades.filter((row) => row.symbol === symbol).sort((a, b) => Date.parse(a.timestamp) - Date.parse(b.timestamp));
  const out: ChartMarker[] = [];
  let held = 0;
  let cost = 0;
  let drawn = false;
  for (const row of rows) {
    const at = epoch(row.timestamp);
    if (from && !until && drawn && held <= 0) break;
    const buy = row.side.toUpperCase() === "BUY";
    const average = held > 0 ? cost / held : row.price;
    if (at >= start && at <= end) {
      drawn = true;
      const units = row.lots > 1 && row.lot_size > 1 ? `${row.lots}L` : `${row.quantity}`;
      out.push(buy
        ? { timestamp: at, price: row.price, label: `${held > 0 ? "ADD" : "BUY"} ${units}`, tone: held > 0 ? "add" : "entry" }
        : { timestamp: at, price: row.price, label: `SELL ${units}`, tone: row.price < average ? "exit_loss" : "exit" });
    }
    if (buy) { held += row.quantity; cost += row.quantity * row.price; }
    else {
      const sold = Math.min(row.quantity, held);
      cost -= sold * average;
      held -= sold;
      if (held <= 0) { held = 0; cost = 0; }
    }
  }
  return out;
}

/** Average cost, hard stop, trail and target as chart lines; missing or non-positive prices are left out. */
export function positionLevels(position: LevelSource | null | undefined): ChartLevel[] {
  if (!position) return [];
  const rows: [number | null | undefined, string, ChartLevel["tone"]][] = [
    [position.average_price, "Avg", "avg"], [position.hard_stop, "Stop", "stop"],
    [position.trailing_stop, "Trail", "trail"], [position.target_price, "Target", "target"],
  ];
  return rows.filter(([price]) => typeof price === "number" && Number.isFinite(price) && price > 0)
    .map(([price, label, tone]) => ({ price: price as number, label, tone }));
}

/** Draw-downs of an equity curve as a fraction below its running peak (0 at a new high). */
export function drawdowns(values: readonly number[]): number[] {
  let peak = Number.NEGATIVE_INFINITY;
  return values.map((value) => {
    peak = Math.max(peak, value);
    return peak > 0 ? value / peak - 1 : 0;
  });
}
