/** Pure helpers for the MCX lane page: types, settings form and display rules. */

export const MCX_ROOTS = ["GOLD", "SILVER", "CRUDEOIL", "COPPER", "NATURALGAS"] as const;
export const ROOT_LABEL: Record<string, string> = {
  GOLD: "Gold", SILVER: "Silver", CRUDEOIL: "Crude oil", COPPER: "Copper", NATURALGAS: "Natural gas",
};
export const ROOT_UNIT: Record<string, string> = {
  GOLD: "₹/10 g · 1 kg lot", SILVER: "₹/kg · 30 kg lot", CRUDEOIL: "₹/bbl · 100 bbl lot",
  COPPER: "₹/kg · 2,500 kg lot", NATURALGAS: "₹/mmBtu · 1,250 mmBtu lot",
};

export type McxSettings = {
  enabled: boolean; auto_trade: boolean; roots_csv: string; initial_capital: number; target_notional: number;
  max_entry_lots: number; max_positions: number; timeframe_seconds: number; min_days_to_expiry: number;
  expiry_flatten_ist: string; rest_fallback_seconds: number; lot_multipliers_csv: string; history_downloads?: boolean;
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
  enabled: boolean; auto_trade: boolean; roots: string[]; timeframe_seconds: number;
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

export type McxSnapshot = {
  lane: "mcx"; settings: McxSettings; health: McxHealth; risk: McxRisk; contracts: McxContractRow[];
  portfolio: McxPortfolio; closed_positions: McxClosed[]; decisions: McxDecision[];
};

export type McxForm = {
  enabled: boolean; auto_trade: boolean; roots: string[]; initial_capital: string; target_notional: string;
  max_entry_lots: string; max_positions: string; timeframe_minutes: string; min_days_to_expiry: string;
  expiry_flatten_ist: string; rest_fallback_seconds: string; history_downloads: boolean;
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
    },
  };
}

/** Only the fields that differ, so a save never rewrites values the owner did not touch. */
export function changedSettings(current: McxSettings, next: Payload): Partial<Payload> {
  const changes: Partial<Payload> = {};
  (Object.keys(next) as (keyof Payload)[]).forEach((key) => {
    const before = key === "roots_csv" ? orderedRoots(rootsFrom(current.roots_csv)).join(",") : current[key];
    if (key === "history_downloads" ? Boolean(before) !== Boolean(next[key]) : before !== next[key]) (changes as Record<string, unknown>)[key] = next[key];
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
