export type PriceViewport = {
  topPrice: number;
  bottomPrice: number;
  rows: number;
  rowHeight: number;
  hiddenAbove: number;
  hiddenBelow: number;
  fullRows: number;
};

export type FootprintColumns = {
  left: number;
  width: number;
  right: number;
  usedWidth: number;
};

/** Keep the newest footprint beside the price scale without stretching sparse sessions. */
export function footprintColumns(plotWidth: number, barCount: number): FootprintColumns {
  const count = Math.max(1, Math.floor(barCount));
  const available = Math.max(1, plotWidth);
  const width = Math.min(152, available / count);
  const usedWidth = width * count;
  const left = Math.max(0, available - usedWidth);
  return { left, width, right: left + usedWidth, usedWidth };
}

/** A marker spanning the whole cropped window still intersects it. */
export function priceRangesOverlap(from: number, to: number, viewBottom: number, viewTop: number): boolean {
  return Math.max(from, to) >= viewBottom && Math.min(from, to) <= viewTop;
}

/** Keep each exchange-price row legible. Price is cropped, never rebucketed. */
export function priceViewport(
  low: number,
  high: number,
  rowSize: number,
  plotHeight: number,
  anchor: number,
  requestedRows?: number | null,
): PriceViewport {
  const bottom = Math.floor(low / rowSize + 1e-7);
  const top = Math.ceil(high / rowSize - 1e-7);
  const fullRows = Math.max(1, top - bottom + 1);
  const readableRows = Math.max(1, Math.floor(plotHeight / 18));
  const rows = Math.max(1, Math.min(fullRows, Math.round(requestedRows ?? readableRows)));
  const anchorRow = Number.isFinite(anchor) ? Math.round(anchor / rowSize) : top;
  const unclampedTop = anchorRow + Math.floor(rows / 2);
  const viewTop = Math.max(bottom + rows - 1, Math.min(top, unclampedTop));
  const viewBottom = viewTop - rows + 1;
  return {
    topPrice: viewTop * rowSize,
    bottomPrice: viewBottom * rowSize,
    rows,
    rowHeight: Math.min(28, plotHeight / rows),
    hiddenAbove: top - viewTop,
    hiddenBelow: viewBottom - bottom,
    fullRows,
  };
}
