import type { ChartLevel, ChartMarker, Trade } from "./types";

// What a lane's position chart draws on top of the bars: every fill of that symbol (or of one position, between
// its entry and exit) and the position's working prices. Pure, so the tests can pin it without a chart.

export type LevelSource = {
  average_price?: number | null; hard_stop?: number | null; trailing_stop?: number | null; target_price?: number | null;
};

const epoch = (iso: string) => Math.floor(Date.parse(iso) / 1000);

/** Markers for one symbol's fills, oldest first. Within [from, until] when given (one position's life, inclusive
 * of a few seconds' slack for fill stamps); with `from` and no `until` (null or omitted) the window closes at the
 * fill that takes the position flat, so a later re-entry is not drawn on an earlier position. From flat, a buy is
 * BUY and a sell is SHORT (a book that sells short, as the spot lane's futures); more in the same direction is ADD.
 * Closing a long is SELL, red when below the running average; closing a short is COVER, red when above it. */
export function fillMarkers(trades: readonly Trade[], symbol: string, from?: string, until?: string | null): ChartMarker[] {
  const start = from ? epoch(from) - 5 : Number.NEGATIVE_INFINITY;
  const end = until ? epoch(until) + 5 : Number.POSITIVE_INFINITY;
  const rows = trades.filter((row) => row.symbol === symbol).sort((a, b) => Date.parse(a.timestamp) - Date.parse(b.timestamp));
  const out: ChartMarker[] = [];
  let held = 0;            // signed: + long, - short
  let cost = 0;            // |held| x average
  let drawn = false;
  for (const row of rows) {
    const at = epoch(row.timestamp);
    if (from && !until && drawn && held === 0) break;
    const buy = row.side.toUpperCase() === "BUY";
    const signed = buy ? row.quantity : -row.quantity;
    const average = held !== 0 ? cost / Math.abs(held) : row.price;
    const opening = held === 0 || Math.sign(held) === Math.sign(signed);
    if (at >= start && at <= end) {
      drawn = true;
      const units = row.lots > 1 && row.lot_size > 1 ? `${row.lots}L` : `${row.quantity}`;
      out.push(opening
        ? { timestamp: at, price: row.price, label: `${held !== 0 ? "ADD" : buy ? "BUY" : "SHORT"} ${units}`, tone: held !== 0 ? "add" : buy ? "entry" : "short" }
        : held > 0
          ? { timestamp: at, price: row.price, label: `SELL ${units}`, tone: row.price < average ? "exit_loss" : "exit" }
          : { timestamp: at, price: row.price, label: `COVER ${units}`, tone: row.price > average ? "cover_loss" : "cover" });
    }
    if (opening) { held += signed; cost += row.quantity * row.price; }
    else {
      const closed = Math.min(row.quantity, Math.abs(held));
      cost -= closed * average;
      held += held > 0 ? -closed : closed;
      if (held === 0) cost = 0;
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
