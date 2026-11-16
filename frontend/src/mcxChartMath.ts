import { IST_OFFSET } from "./chartMath.ts";
import type { Candle } from "./types.ts";

export type McxStreamMinute = { row: Candle; seen: number };

function bucket(timestamp: number, frame: number): number {
  const open = Math.floor((timestamp + IST_OFFSET) / 86_400) * 86_400 - IST_OFFSET + 9 * 3600;
  return open + Math.floor((timestamp - open) / frame) * frame;
}

/** Combine one candle per minute, using the newest observation of that minute. */
export function mcxFormingBar(
  stored: Candle[], streamed: McxStreamMinute[], observed: number, after: number, frame: number,
): Candle | undefined {
  const rows = new Map(stored.filter(row => bucket(row.timestamp, frame) > after).map(row => [row.timestamp, row]));
  for (const { row, seen } of streamed) {
    if (bucket(row.timestamp, frame) <= after) continue;
    const base = rows.get(row.timestamp);
    if (!base || seen >= observed || (row.closed && !base.closed)) rows.set(row.timestamp, row);
  }
  const ordered = [...rows.values()].sort((a, b) => a.timestamp - b.timestamp);
  const last = ordered.at(-1);
  if (!last) return undefined;
  const start = bucket(last.timestamp, frame);
  const tail = ordered.filter(row => row.timestamp >= start);
  return {
    symbol: last.symbol, timestamp: start, open: tail[0].open, close: last.close,
    high: Math.max(...tail.map(row => row.high)), low: Math.min(...tail.map(row => row.low)),
    volume: tail.reduce((sum, row) => sum + row.volume, 0), closed: false,
  };
}
