import { useEffect, useState } from "react";
import { API_TOKEN, API_URL } from "./runtime";
import {
  SHORT_VERDICT, TRIGGER_LABEL, shortChanges, toShortForm,
  type McxShortForm, type McxShorts, type McxSnapshot,
} from "./mcxMath";

/** The MCX selling book: writes ATM options on a premium cross down and on the
 * opposite leg of every buy signal, with a SPAN-style margin. Paper only. */

const authHeaders = (): Record<string, string> => (API_TOKEN ? { "x-macd-token": API_TOKEN } : {});
const number = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2 });
const finite = (value?: number | null): value is number => typeof value === "number" && Number.isFinite(value);
const currency = (value?: number | null) => (finite(value) ? `₹${number.format(Math.round(value))}` : "—");
const fixed = (value?: number | null, digits = 2) => (finite(value) ? number.format(Number(value.toFixed(digits))) : "—");
const tone = (value?: number | null) => (finite(value) ? (value >= 0 ? "positive" : "negative") : "");
const short = (symbol: string) => symbol.split(":")[1] ?? symbol;
const STAMP = new Intl.DateTimeFormat("en-IN", { timeZone: "Asia/Kolkata", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: false });
const stamp = (value?: string | null) => (value ? STAMP.format(new Date(value)) : "—");
const verdict = (key: string) => SHORT_VERDICT[key] ?? { label: key, help: "" };
const trigger = (key: string) => TRIGGER_LABEL[key] ?? key;
const Head = ({ labels }: { labels: string[] }) =>
  <thead><tr>{labels.map((label) => <th key={label}><button tabIndex={-1}>{label}</button></th>)}</tr></thead>;

export function SellingTab({ snapshot, onSaved }: { snapshot: McxSnapshot; onSaved: (next: McxSnapshot) => void }) {
  const book = snapshot.shorts;
  if (!book) return <section className="panel"><p className="blast-note">The selling book is not running on this engine yet.</p></section>;
  return <div className="mcx-sell">
    <section className="blast-metrics" aria-label="MCX selling book">
      <div><span>Selling equity</span><b>{currency(book.account.equity)}</b></div>
      <div><span>Margin used</span><b>{currency(book.account.margin_used)}</b></div>
      <div><span>Free margin</span><b className={tone(book.account.free_margin)}>{currency(book.account.free_margin)}</b></div>
      <div><span>Realized</span><b className={tone(book.account.realized)}>{currency(book.account.realized)}</b></div>
      <div><span>Unrealized</span><b className={tone(book.account.unrealized)}>{currency(book.account.unrealized)}</b></div>
      <div><span>Shorts</span><b>{book.open.length} open · {book.settings.auto_trade ? "writing" : "selling off"}</b></div>
    </section>
    <ShortPositions book={book} onSaved={onSaved} />
    <ShortDecisions book={book} />
    <MarginTable book={book} />
    <ShortSettings book={book} onSaved={onSaved} />
  </div>;
}

function ShortPositions({ book, onSaved }: { book: McxShorts; onSaved: (next: McxSnapshot) => void }) {
  const [message, setMessage] = useState("");
  async function cover(symbol: string) {
    if (!window.confirm(`Buy back the paper short in ${short(symbol)} now?`)) return;
    setMessage("");
    const response = await fetch(`${API_URL}/api/mcx/shorts/cover`, {
      method: "POST", headers: { "content-type": "application/json", ...authHeaders() }, body: JSON.stringify({ symbol }),
    });
    const body: unknown = await response.json().catch(() => undefined);
    if (response.ok) onSaved(body as McxSnapshot);
    else setMessage(((body as { detail?: string } | undefined)?.detail) || `Request failed (HTTP ${response.status})`);
  }
  return <>
    <section className="panel">
      <div className="panel-title"><span>Open shorts</span><span>{book.open.length}</span></div>
      {message && <div className="settings-message" role="status">{message}</div>}
      <div className="ledger-table-wrap"><table className="ledger-table">
        <Head labels={["Contract", "Lots", "Sold at", "Last", "P&L", "Margin", "IV", "Hard stop", "Trail", "Trigger", "Opened", ""]} />
        <tbody>{book.open.length === 0 ? <tr><td className="empty" colSpan={12}>No open shorts</td></tr> : book.open.map((row) => <tr key={row.short_id}>
          <td>{short(row.symbol)}</td><td>{row.lots} × {number.format(row.lot_size)}</td><td>{fixed(row.entry_price)}</td><td>{fixed(row.last_price)}</td>
          <td className={tone(row.unrealized)}>{currency(row.unrealized)}</td><td>{currency(row.margin_now)}</td><td>{fixed(row.iv * 100, 1)}%</td>
          <td>{fixed(row.hard_stop)}</td><td>{fixed(row.trail_stop)}</td><td>{trigger(row.trigger)}</td><td>{stamp(row.entry_at)}</td>
          <td>{row.pending_exit ? <span title={row.pending_exit}>buying back…</span> : <button className="secondary" onClick={() => void cover(row.symbol)}>Buy back</button>}</td>
        </tr>)}</tbody>
      </table></div>
    </section>
    <section className="panel">
      <div className="panel-title"><span>Closed shorts</span><span>newest 100</span></div>
      <div className="ledger-table-wrap"><table className="ledger-table">
        <Head labels={["Contract", "Qty", "Sold at", "Bought at", "P&L", "Reason", "Trigger", "Closed"]} />
        <tbody>{book.closed.length === 0 ? <tr><td className="empty" colSpan={8}>Nothing closed yet</td></tr> : book.closed.map((row) => <tr key={row.short_id}>
          <td>{short(row.symbol)}</td><td>{number.format(row.quantity)}</td><td>{fixed(row.entry_price)}</td><td>{fixed(row.exit_price)}</td>
          <td className={tone(row.pnl)}>{currency(row.pnl)}</td><td>{row.exit_reason ?? "—"}</td><td>{trigger(row.trigger)}</td><td>{stamp(row.exit_at)}</td>
        </tr>)}</tbody>
      </table></div>
    </section>
  </>;
}

function ShortDecisions({ book }: { book: McxShorts }) {
  return <section className="panel">
    <div className="panel-title"><span>Selling triggers</span><span>newest 60 since the engine started</span></div>
    <p className="blast-note">A premium MACD cross down of an ATM option writes it; a buy signal writes the other leg of that commodity. The option's own cross up, an opposite trigger, the stops or the expiry exit buy it back.</p>
    <div className="ledger-table-wrap"><table className="ledger-table">
      <Head labels={["Time", "Contract", "Trigger", "Premium", "Verdict", "Lots", "Margin / lot", "Detail"]} />
      <tbody>{book.decisions.length === 0 ? <tr><td className="empty" colSpan={8}>No triggers yet</td></tr> : book.decisions.map((row, index) => <tr key={`${row.at}-${row.symbol}-${index}`}>
        <td>{stamp(row.at)}</td><td>{short(row.symbol)}</td>
        <td>{trigger(row.trigger)}{row.source ? <small> · from {short(row.source)}</small> : null}</td><td>{fixed(row.price)}</td>
        <td className={row.verdict === "SHORT_TAKEN" ? "positive" : ""} title={verdict(row.verdict).help}>{verdict(row.verdict).label}</td>
        <td>{row.lots || "—"}</td><td>{currency(row.margin_per_lot)}</td><td>{row.detail || ""}</td>
      </tr>)}</tbody>
    </table></div>
  </section>;
}

function MarginTable({ book }: { book: McxShorts }) {
  return <section className="panel">
    <div className="panel-title"><span>Margin for one short lot</span><span>SPAN-style estimate · ticket {currency(book.settings.ticket)}</span></div>
    <p className="blast-note">Worst of 16 futures-price and volatility scenarios (extreme moves at {fixed(book.settings.extreme_cover * 100, 0)}%), at least the short option minimum, plus extreme loss margin. Estimates, not MCXCCL's daily risk files.</p>
    <div className="ledger-table-wrap"><table className="ledger-table">
      <Head labels={["Contract", "Premium", "IV", "Scenario risk", "Minimum", "SPAN", "ELM", "Margin / lot", "Lots in ticket"]} />
      <tbody>{book.margin_quotes.length === 0 ? <tr><td className="empty" colSpan={9}>No prices yet</td></tr> : book.margin_quotes.map((row) => <tr key={row.symbol}>
        <td>{short(row.symbol)}</td><td>{fixed(row.premium)}</td><td>{fixed(row.iv * 100, 1)}%</td><td>{currency(row.scanning_risk)}</td>
        <td>{currency(row.short_option_minimum)}</td><td>{currency(row.span)}</td><td>{currency(row.elm)}</td><td><b>{currency(row.total)}</b></td>
        <td className={row.lots_in_ticket ? "" : "negative"}>{row.lots_in_ticket || "above ticket"}</td>
      </tr>)}</tbody>
    </table></div>
  </section>;
}

function ShortSettings({ book, onSaved }: { book: McxShorts; onSaved: (next: McxSnapshot) => void }) {
  const [form, setForm] = useState<McxShortForm>(() => toShortForm(book.settings));
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  useEffect(() => { if (!dirty) setForm(toShortForm(book.settings)); }, [book.settings, dirty]);
  const set = <K extends keyof McxShortForm>(key: K, value: McxShortForm[K]) => {
    setForm((current) => ({ ...current, [key]: value })); setDirty(true); setError(""); setMessage("");
  };
  const field = (key: Exclude<keyof McxShortForm, "auto_trade" | "price_scan_csv">, label: string, help: string, step = "any") =>
    <label>{label}
      <input type="number" inputMode="decimal" step={step} value={form[key]} onChange={(event) => set(key, event.target.value)} />
      <p className="field-help">{help}</p>
    </label>;
  async function save() {
    const parsed = shortChanges(book.settings, form);
    if (!parsed.payload) { setError(parsed.error || "Check the values"); return; }
    if (!Object.keys(parsed.payload).length) { setDirty(false); setMessage("Nothing to save."); return; }
    setSaving(true); setError(""); setMessage("");
    try {
      const response = await fetch(`${API_URL}/api/mcx/settings`, {
        method: "PUT", headers: { "content-type": "application/json", ...authHeaders() }, body: JSON.stringify(parsed.payload),
      });
      const body: unknown = await response.json().catch(() => undefined);
      if (!response.ok) throw new Error(((body as { detail?: string } | undefined)?.detail) || `Request failed (HTTP ${response.status})`);
      const next = body as McxSnapshot;
      onSaved(next);
      if (next.shorts) setForm(toShortForm(next.shorts.settings));
      setDirty(false); setMessage("Saved.");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Request failed");
    } finally {
      setSaving(false);
    }
  }
  return <section className="panel blast-settings">
    <div className="settings-section">
      <h3>Selling book</h3>
      <label className="toggle"><input type="checkbox" checked={form.auto_trade} onChange={(event) => set("auto_trade", event.target.checked)} />Write options on selling triggers (paper)</label>
      <p className="field-help">Off records every trigger with its verdict and writes nothing. Open shorts keep their stops and buy-back rules.</p>
      <div className="settings-grid three">
        {field("capital", "Capital · ₹", "The selling book's own paper capital. Margin is blocked against it.", "10000")}
        {field("ticket", "Margin ticket per short · ₹", "Whole lots whose margin fits under this.", "10000")}
        {field("max_lots", "Max lots per short", "Ceiling for cheap minis.", "1")}
        {field("max_positions", "Max open shorts", "1 = one short at a time.", "1")}
      </div>
    </div>
    <div className="settings-section">
      <h3>Margin model (estimates)</h3>
      <label>Price scan range · % of futures, per commodity
        <input type="text" value={form.price_scan_csv} onChange={(event) => set("price_scan_csv", event.target.value)} />
        <p className="field-help">One-day futures move the margin must cover, like MCX initial margin. Minis use their full-size commodity.</p>
      </label>
      <div className="settings-grid three">
        {field("volatility_scan_pct", "Volatility scan · ±% of IV", "Implied volatility up and down in the scenarios.")}
        {field("extreme_cover", "Extreme-move weight", "Two scenarios at twice the price scan, counted at this fraction.")}
        {field("min_charge_pct", "Short option minimum · % notional", "Floor for far out-of-the-money shorts.")}
        {field("elm_pct", "Extreme loss margin · % notional", "Added on top of SPAN.")}
      </div>
    </div>
    {error && <div className="settings-error" role="alert">{error}</div>}
    {message && <div className="settings-message" role="status">{message}</div>}
    <div className="settings-actions">
      <button className="secondary" disabled={saving || !dirty} onClick={() => { setForm(toShortForm(book.settings)); setDirty(false); setError(""); setMessage(""); }}>Discard changes</button>
      <button className="primary" disabled={saving || !dirty} onClick={() => void save()}>{saving ? "Saving…" : "Save selling settings"}</button>
    </div>
  </section>;
}
