/** Pure helpers for the MCX lane page: types, settings form and display rules. */

export const MCX_ROOTS = ["GOLD", "SILVER", "CRUDEOIL", "COPPER", "NATURALGAS"] as const;
export const ROOT_LABEL: Record<string, string> = {
  GOLD: "Gold", SILVER: "Silver", CRUDEOIL: "Crude oil", COPPER: "Copper", NATURALGAS: "Natural gas",
  GOLDM: "Gold mini", SILVERM: "Silver mini", CRUDEOILM: "Crude oil mini", NATGASMINI: "Natural gas mini",
};
export const ROOT_UNIT: Record<string, string> = {
  GOLD: "₹/10 g · 1 kg lot", SILVER: "₹/kg · 30 kg lot", CRUDEOIL: "₹/bbl · 100 bbl lot",
  COPPER: "₹/kg · 2,500 kg lot", NATURALGAS: "₹/mmBtu · 1,250 mmBtu lot",
  GOLDM: "₹/10 g · 100 g lot", SILVERM: "₹/kg · 5 kg lot", CRUDEOILM: "₹/bbl · 10 bbl lot",
  NATGASMINI: "₹/mmBtu · 250 mmBtu lot",
};

export type McxSettings = {
  enabled: boolean; auto_trade: boolean; roots_csv: string; initial_capital: number; target_notional: number;
  max_entry_lots: number; max_positions: number; timeframe_seconds: number; min_days_to_expiry: number;
  expiry_flatten_ist: string; rest_fallback_seconds: number; lot_multipliers_csv: string; history_downloads?: boolean;
  ticket_ceiling?: boolean; opposite_cross_exit?: boolean;
};

export type McxContractRow = {
  root: string; display: string; symbol: string; option_type: "CE" | "PE" | "FUT"; strike: number; expiry: string;
  futures_symbol: string; futures_expiry: string; selection_price: number; lot_size: number; exchange_lot: number;
  volume: number; oi: number; retained: boolean; ltp: number | null; futures_ltp: number | null;
  macd: number | null; signal_line: number | null; histogram: number | null; bar: number | null; bars: number;
  warming: boolean; ws_age_seconds: number | null; held_lots: number; days_to_expiry: number | null;
  evaluation: { cross: boolean; fired: boolean; passed: number; required_count: number } | null;
};

export type McxDecision = {
  at: string; symbol: string; root: string; option_type: string; verdict: string; price: number; macd: number;
  bar: number | null; lots: number; detail: string; order_id: string | null;
};

export type McxPosition = {
  symbol: string; quantity: number; lots: number; lot_size: number; average_price: number; last_price: number;
  unrealized_pnl: number; return_pct: number; hard_stop: number; trailing_stop: number | null; opened_at: string;
  max_return_pct: number;
};

export type McxClosed = {
  symbol: string; quantity: number; entry_price: number; exit_price: number; realized_pnl: number; return_pct: number;
  exit_reason: string | null; exit_time: string; entry_time?: string;
};

export type McxPortfolio = {
  initial_capital: number; cash: number; market_value: number; equity: number; realized_pnl: number;
  unrealized_pnl: number; positions: McxPosition[]; closed_positions?: McxClosed[];
};

export type McxHealth = {
  enabled: boolean; auto_trade: boolean; roots: string[]; mini_roots?: string[]; timeframe_seconds: number;
  session: { open: boolean; phase: string; opens_at: string; closes_at: string; next_open: string;
    holiday: string | null; calendar_covers_year: boolean };
  selection: { date: string | null; selected_at: string | null; source?: string; errors: Record<string, string>;
    roots: Record<string, { expiry: string; futures: string; reference: number; rolled: boolean; lot_multiplier: number }> };
  contracts: number; held: number; subscribed: number;
  warmup: { state?: string; symbols?: number; loaded?: number; failed?: Record<string, string>; finished_at?: string;
    from_store?: number; downloaded?: number; held?: number };
  futures?: number; ladder?: number;
  store?: { contracts?: number; downloaded_once?: number; bars?: number; first_bar?: number | null; last_bar?: number | null;
    feed_gaps?: number; megabytes?: number };
  history?: { downloads_allowed: boolean; requests: number; bars: number; contracts: number; last_at: string | null;
    failed_count: number; backfill_running: boolean };
  ws_ticks_today: number; rest_fallback: { seconds: number; polls: number; ticks: number; error: string | null };
  signals_emitted: number; decisions: Record<string, number>; multiplier_warnings: Record<string, string>;
  lane_errors: Record<string, { count: number; last: string; at: string }>; error: string | null;
};

export type McxRisk = {
  inherited_from: string; entry_filter: string; macd_periods: number[]; hard_stop_pct: number;
  trailing_activation_profit_pct: number; trailing_stop_pct: number; max_trade_lots: number;
  scale_in_profit_pct: number[]; scale_out_profit_pct: number[]; macd_invalidation_exit: boolean;
  slippage_bps: number; target_position_notional: number; max_positions: number;
};

export const MCX_QUOTES_EVENT = "macd:mcx-quotes";
export type McxQuote = {
  symbol: string; ltp: number; source: "ws" | "rest"; move: "up" | "down" | null;
  seen_ms: number; updated_ms: number; price_ms: number; day: string;
  prev_close: number | null; change: number | null; change_pct: number | null; volume: number;
  oi: number | null; oi_change: number | null; oi_ms: number | null;
};

export function newestMcxQuote(snapshot: McxQuote | undefined, live: McxQuote | undefined) {
  return live && (!snapshot || live.updated_ms >= snapshot.updated_ms) ? live : snapshot;
}

export type McxSnapshot = {
  lane: "mcx"; settings: McxSettings; health: McxHealth; risk: McxRisk; contracts: McxContractRow[];
  portfolio: McxPortfolio; closed_positions: McxClosed[]; decisions: McxDecision[];
  quotes?: Record<string, McxQuote>;
  shorts?: McxShorts;
};

// ------------------------------------------------------------ selling book

export type McxShortSettings = {
  auto_trade: boolean; capital: number; ticket: number; max_lots: number; max_positions: number;
  price_scan_csv: string; volatility_scan_pct: number; extreme_cover: number; min_charge_pct: number; elm_pct: number;
};

export type McxShortRow = {
  short_id: string; symbol: string; root: string; option_type: string; strike: number; expiry: string;
  lots: number; lot_size: number; quantity: number; entry_price: number; entry_at: string; trigger: string;
  margin: number; margin_now: number; iv: number; last_price: number; low_price: number; high_price: number;
  status: string; exit_price: number | null; exit_at: string | null; exit_reason: string | null; pnl: number;
  unrealized: number; hard_stop: number | null; trail_stop: number | null; pending_exit: string | null;
};

export type McxShortDecision = {
  at: string; symbol: string; root: string; option_type: string; trigger: string; source: string | null;
  verdict: string; price: number | null; macd: number | null; lots: number; detail: string; margin_per_lot: number | null;
};

export type McxMarginQuote = {
  symbol: string; root: string; option_type: string; premium: number; lot_size: number; iv: number;
  scanning_risk: number; short_option_minimum: number; span: number; elm: number; total: number; lots_in_ticket: number;
};

export type McxShorts = {
  settings: McxShortSettings;
  account: { capital: number; realized: number; realized_today: number; unrealized: number; equity: number;
    margin_used: number; free_margin: number };
  open: McxShortRow[]; closed: McxShortRow[]; decisions: McxShortDecision[]; margin_quotes: McxMarginQuote[];
  errors: Record<string, string>;
};

export const SHORT_VERDICT: Record<string, { label: string; help: string }> = {
  SHORT_TAKEN: { label: "Written", help: "Sold in the selling book." },
  SHORT_AUTO_OFF: { label: "Selling off", help: "A valid trigger; the selling book is set not to write." },
  ALREADY_SHORT: { label: "Already short", help: "The book is already short this contract." },
  SHORT_FULL: { label: "Book full", help: "The selling book already holds its one short." },
  NO_PRICE: { label: "No fresh price", help: "No websocket price for the option in the last few seconds." },
  NO_FUTURES: { label: "No futures price", help: "Margin needs the underlying futures price." },
  MARGIN_ABOVE_TICKET: { label: "Margin above ticket", help: "One lot needs more margin than the per-position ticket." },
  NO_MARGIN: { label: "No free margin", help: "Free margin does not cover one lot." },
  SESSION_CLOSED: { label: "Session closed", help: "MCX was shut." },
  NOT_SELECTED: { label: "Not selected", help: "Shorts use today's ATM legs only." },
  EXPIRY_DAY: { label: "Expiry day", help: "Never written on its expiry day." },
};

export const TRIGGER_LABEL: Record<string, string> = {
  PREMIUM_MACD_ZERO_CROSS_DOWN: "Premium crossed down",
  OPPOSITE_LEG_OF_BUY: "Opposite leg of a buy",
};

export type McxShortForm = {
  auto_trade: boolean; capital: string; ticket: string; max_lots: string; max_positions: string; price_scan_csv: string;
  volatility_scan_pct: string; extreme_cover: string; min_charge_pct: string; elm_pct: string;
};

export function toShortForm(settings: McxShortSettings): McxShortForm {
  return {
    auto_trade: settings.auto_trade, capital: String(settings.capital), ticket: String(settings.ticket),
    max_lots: String(settings.max_lots), max_positions: String(settings.max_positions), price_scan_csv: settings.price_scan_csv,
    volatility_scan_pct: String(settings.volatility_scan_pct), extreme_cover: String(settings.extreme_cover),
    min_charge_pct: String(settings.min_charge_pct), elm_pct: String(settings.elm_pct),
  };
}

/** The changed selling settings as the API's short_* fields, or the first error. */
export function shortChanges(current: McxShortSettings, form: McxShortForm): { payload?: Record<string, unknown>; error?: string } {
  const num = (raw: string) => (raw.trim() === "" ? Number.NaN : Number(raw));
  const next: McxShortSettings = {
    auto_trade: form.auto_trade, capital: num(form.capital), ticket: num(form.ticket), max_lots: num(form.max_lots),
    max_positions: num(form.max_positions), price_scan_csv: form.price_scan_csv.trim().toUpperCase().replace(/\s+/g, ""),
    volatility_scan_pct: num(form.volatility_scan_pct), extreme_cover: num(form.extreme_cover),
    min_charge_pct: num(form.min_charge_pct), elm_pct: num(form.elm_pct),
  };
  if (!(next.capital >= 1)) return { error: "Capital must be at least ₹1." };
  if (!(next.ticket >= 1)) return { error: "Margin ticket must be at least ₹1." };
  if (!Number.isInteger(next.max_lots) || next.max_lots < 1 || next.max_lots > 1000) return { error: "Max lots must be a whole number from 1 to 1000." };
  if (!Number.isInteger(next.max_positions) || next.max_positions < 1 || next.max_positions > 20) return { error: "Max shorts must be 1 to 20." };
  if (!/^([A-Z0-9]+:\d+(\.\d+)?)(,[A-Z0-9]+:\d+(\.\d+)?)*$/.test(next.price_scan_csv)) return { error: "Price scan must look like GOLD:8,SILVER:12." };
  for (const key of ["volatility_scan_pct", "min_charge_pct", "elm_pct"] as const) {
    if (!(next[key] >= 0 && next[key] <= 100)) return { error: "Percentages must be 0 to 100." };
  }
  if (!(next.extreme_cover >= 0 && next.extreme_cover <= 1)) return { error: "Extreme-move weight must be 0 to 1." };
  const payload: Record<string, unknown> = {};
  (Object.keys(next) as (keyof McxShortSettings)[]).forEach((key) => {
    if (next[key] !== current[key]) payload[`short_${key}`] = next[key];
  });
  return { payload };
}

export type McxForm = {
  enabled: boolean; auto_trade: boolean; roots: string[]; initial_capital: string; target_notional: string;
  max_entry_lots: string; max_positions: string; timeframe_minutes: string; min_days_to_expiry: string;
  expiry_flatten_ist: string; rest_fallback_seconds: string; history_downloads: boolean;
  ticket_ceiling: boolean; opposite_cross_exit: boolean;
};

export const VERDICT_LABEL: Record<string, { label: string; help: string }> = {
  TAKEN: { label: "Taken", help: "Bought in the lane's paper book." },
  AUTO_TRADE_OFF: { label: "Auto-trade off", help: "A valid zero-cross; the lane is set not to buy." },
  ALREADY_HELD: { label: "Already held", help: "The lane already holds this contract; it never adds on a new signal." },
  NO_CASH: { label: "No cash", help: "One lot costs more than the lane's free cash." },
  ENTRY_REJECTED: { label: "Rejected", help: "The paper order was refused (stale quote, position cap, cash)." },
  STALE_BAR: { label: "Stale bar", help: "The signal's bar belongs to an earlier period than now." },
  SESSION_CLOSED: { label: "Session closed", help: "Fired on the closing bar after MCX had shut." },
  NOT_SELECTED: { label: "Not selected", help: "A holding carried from an earlier selection; entries use today's ATM legs." },
  LANE_FULL: { label: "Lane full", help: "The lane already holds its one position; this signal waits for it to close." },
  LOT_ABOVE_TICKET: { label: "Lot above ticket", help: "One lot costs more than the per-position ticket; the commodity's mini carries it." },
  DISABLED: { label: "Lane off", help: "The lane is switched off." },
};

export const verdictMeta = (verdict: string) => VERDICT_LABEL[verdict] ?? { label: verdict, help: "" };

export function rootsFrom(csv: string): string[] {
  return [...new Set(csv.split(",").map((item) => item.trim().toUpperCase()).filter(Boolean))];
}

/** Known commodities in their display order, then any others as given. */
export function orderedRoots(roots: string[]): string[] {
  const known: readonly string[] = MCX_ROOTS;
  return [...known.filter((root) => roots.includes(root)), ...roots.filter((root) => !known.includes(root))];
}

export function toForm(settings: McxSettings): McxForm {
  return {
    enabled: settings.enabled,
    auto_trade: settings.auto_trade,
    roots: rootsFrom(settings.roots_csv),
    initial_capital: String(settings.initial_capital),
    target_notional: String(settings.target_notional),
    max_entry_lots: String(settings.max_entry_lots),
    max_positions: String(settings.max_positions),
    timeframe_minutes: String(settings.timeframe_seconds / 60),
    min_days_to_expiry: String(settings.min_days_to_expiry),
    expiry_flatten_ist: settings.expiry_flatten_ist,
    rest_fallback_seconds: String(settings.rest_fallback_seconds),
    history_downloads: Boolean(settings.history_downloads),
    ticket_ceiling: settings.ticket_ceiling !== false,
    opposite_cross_exit: settings.opposite_cross_exit !== false,
  };
}

type Payload = Omit<McxSettings, "lot_multipliers_csv">;

export function fromForm(form: McxForm): { payload?: Payload; error?: string } {
  const num = (raw: string) => (raw.trim() === "" ? Number.NaN : Number(raw));
  const capital = num(form.initial_capital);
  const notional = num(form.target_notional);
  const lots = num(form.max_entry_lots);
  const positions = num(form.max_positions);
  const minutes = num(form.timeframe_minutes);
  const days = num(form.min_days_to_expiry);
  const fallback = num(form.rest_fallback_seconds);
  if (!form.roots.length) return { error: "Choose at least one commodity." };
  if (!(capital >= 1)) return { error: "Capital must be at least ₹1." };
  if (!(notional >= 0 && notional <= 10_000_000)) return { error: "Size per entry must be between ₹0 and ₹1 crore." };
  if (!Number.isInteger(lots) || lots < 1 || lots > 500) return { error: "Max lots per entry must be a whole number from 1 to 500." };
  if (!Number.isInteger(positions) || positions < 1 || positions > 100) return { error: "Max open positions must be a whole number from 1 to 100." };
  if (!Number.isInteger(minutes) || minutes < 0 || minutes > 60) return { error: "Bar length must be 0 (follow the MACD lane) to 60 minutes." };
  if (!Number.isInteger(days) || days < 0 || days > 30) return { error: "Minimum days to expiry must be 0 to 30." };
  if (!(fallback >= 0 && fallback <= 3600)) return { error: "REST fallback must be 0 (off) to 3600 seconds." };
  const flatten = form.expiry_flatten_ist.trim();
  if (flatten && !/^([01]\d|2[0-3]):[0-5]\d$/.test(flatten)) return { error: "Expiry flatten time must be HH:MM (24-hour IST) or blank." };
  return {
    payload: {
      enabled: form.enabled, auto_trade: form.auto_trade,
      roots_csv: orderedRoots(form.roots).join(","),
      initial_capital: capital, target_notional: notional, max_entry_lots: lots, max_positions: positions,
      timeframe_seconds: minutes * 60, min_days_to_expiry: days, expiry_flatten_ist: flatten, rest_fallback_seconds: fallback,
      history_downloads: form.history_downloads,
      ticket_ceiling: form.ticket_ceiling, opposite_cross_exit: form.opposite_cross_exit,
    },
  };
}

/** Only the fields that differ, so a save never rewrites values the owner did not touch. */
export function changedSettings(current: McxSettings, next: Payload): Partial<Payload> {
  const changes: Partial<Payload> = {};
  (Object.keys(next) as (keyof Payload)[]).forEach((key) => {
    const before = key === "roots_csv" ? orderedRoots(rootsFrom(current.roots_csv)).join(",") : current[key];
    const flag = key === "history_downloads" || key === "ticket_ceiling" || key === "opposite_cross_exit";
    if (flag ? Boolean(before ?? key !== "history_downloads") !== Boolean(next[key]) : before !== next[key]) (changes as Record<string, unknown>)[key] = next[key];
  });
  return changes;
}

/** Points of MACD below/above zero, signed, for a compact cell. */
export function macdState(row: McxContractRow): { label: string; tone: "positive" | "negative" | "" } {
  if (row.warming) return { label: "warming", tone: "" };
  if (row.macd === null || !Number.isFinite(row.macd)) return { label: `${row.bars} bars`, tone: "" };
  return { label: row.macd >= 0 ? "above zero" : "below zero", tone: row.macd >= 0 ? "positive" : "negative" };
}

export function sessionLabel(session: McxHealth["session"]): string {
  if (session.open) return `MCX open · ${session.phase} · closes ${session.closes_at}`;
  if (session.holiday) return `MCX ${session.phase} · ${session.holiday}`;
  return `MCX ${session.phase} · opens ${session.opens_at}`;
}
