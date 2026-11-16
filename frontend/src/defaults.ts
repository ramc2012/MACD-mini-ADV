// Which instrument a pull-down opens on when nothing has been chosen: NIFTY (owner, 16 Nov 2026).

const MONTH_CODES = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"];

/** The nearest-month NIFTY future among the symbols (NSE:NIFTY26NOVFUT), if any. */
export function frontNiftyFuture(symbols: string[]): string | undefined {
  const rank = (s: string) => {
    const m = /^NSE:NIFTY(\d{2})([A-Z]{3})FUT$/.exec(s);
    return m && MONTH_CODES.includes(m[2]) ? Number(m[1]) * 12 + MONTH_CODES.indexOf(m[2]) : null;
  };
  return symbols.filter((s) => rank(s) != null).sort((a, b) => rank(a)! - rank(b)!)[0];
}

export const NIFTY_INDEX = "NSE:NIFTY50-INDEX";

/** The NIFTY 50 index when the list has it, else the list's first entry. */
export function niftyFirst(symbols: string[]): string {
  return symbols.find((s) => s === NIFTY_INDEX) || symbols[0] || "";
}
