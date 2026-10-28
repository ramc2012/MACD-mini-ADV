import {useEffect, useRef, type ReactNode} from 'react';
import {compareKeys, sortKey} from './tableSort';

// Makes every table inside it sortable: click a header to sort ascending, click it again for descending. Rows are reordered within their own tbody (React tolerates sibling moves), and the
// chosen order is re-applied whenever live data re-renders the rows. A full-width row (one cell spanning the table,
// e.g. an expanded detail) travels with the row above it.
type State = {col: number; dir: 1 | -1};

function logicalCell(row: HTMLTableRowElement, col: number): HTMLTableCellElement | null {
  let at = 0;
  for (const cell of Array.from(row.cells)) {
    if (col < at + cell.colSpan) return cell;
    at += cell.colSpan;
  }
  return null;
}

function headerColumn(th: HTMLTableCellElement): number {
  let at = 0;
  for (const cell of Array.from((th.parentElement as HTMLTableRowElement).cells)) {
    if (cell === th) return at;
    at += cell.colSpan;
  }
  return at;
}

export function SortableTables({children}: {children: ReactNode}) {
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = root.current;
    if (!el) return;
    const states = new WeakMap<HTMLTableElement, State>();
    let observer: MutationObserver;
    let frame = 0;

    const apply = (table: HTMLTableElement) => {
      const state = states.get(table);
      if (!state) return;
      const width = table.tHead?.rows[0] ? Array.from(table.tHead.rows[0].cells).reduce((s, c) => s + c.colSpan, 0) : 0;
      for (const body of Array.from(table.tBodies)) {
        const groups: HTMLTableRowElement[][] = [];
        for (const row of Array.from(body.rows)) {
          const detail = row.cells.length === 1 && row.cells[0].colSpan > 1 && row.cells[0].colSpan >= width - 1;
          if (detail && groups.length) groups[groups.length - 1].push(row);
          else groups.push([row]);
        }
        if (groups.length < 2) continue;
        const keyed = groups.map((g, i) => ({g, i, key: sortKey(logicalCell(g[0], state.col)?.textContent ?? '')}));
        keyed.sort((a, b) => compareKeys(a.key, b.key, state.dir) || a.i - b.i);
        if (keyed.every((k, i) => k.i === i)) continue;
        for (const {g} of keyed) for (const row of g) body.appendChild(row);
      }
    };

    const resort = () => {
      frame = 0;
      observer.disconnect();
      el.querySelectorAll('table').forEach(t => { if (states.has(t)) apply(t); });
      observer.observe(el, {childList: true, subtree: true, characterData: true});
    };

    observer = new MutationObserver(() => { if (!frame) frame = requestAnimationFrame(resort); });

    const onClick = (event: MouseEvent) => {
      const th = (event.target as HTMLElement).closest('th');
      const table = th?.closest('table');
      if (!th || !table || !el.contains(table) || !th.closest('thead')) return;
      if ((event.target as HTMLElement).closest('button,a,input,select,label')) return;
      const col = headerColumn(th);
      const now = states.get(table);
      const next: State = {col, dir: now?.col === col && now.dir === 1 ? -1 : 1};
      states.set(table, next);
      table.querySelectorAll('thead th').forEach(h => { h.removeAttribute('data-sort'); h.removeAttribute('aria-sort'); });
      th.setAttribute('data-sort', next.dir === 1 ? 'asc' : 'desc');
      th.setAttribute('aria-sort', next.dir === 1 ? 'ascending' : 'descending');
      observer.disconnect();
      apply(table);
      observer.observe(el, {childList: true, subtree: true, characterData: true});
    };

    el.addEventListener('click', onClick);
    observer.observe(el, {childList: true, subtree: true, characterData: true});
    return () => { el.removeEventListener('click', onClick); observer.disconnect(); if (frame) cancelAnimationFrame(frame); };
  }, []);
  return <div ref={root} className="sortable-tables">{children}</div>;
}
