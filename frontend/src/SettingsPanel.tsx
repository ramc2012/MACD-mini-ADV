import { useEffect, useRef, useState } from "react";
import { readableUpstreamError, responseErrorMessage } from "./apiError";
import { extractAuthCode, tokenLeft } from "./sessionClock";
import { API_TOKEN, API_URL } from "./runtime";

type RuntimeSettings = {
  feed_mode: "simulation" | "fyers";
  execution_mode: "paper";
  symbols: string[];
  timeframe_seconds: number;
  fast_period: number;
  slow_period: number;
  signal_period: number;
  bb_period: number;
  bb_deviations: number;
  kama_period: number;
  kama_fast: number;
  kama_slow: number;
  kama_rsi_period: number;
  kama_rsi_min: number;
  kama_roc_period: number;
  kama_roc_min: number;
  require_kama_confirmation: boolean;
  require_kama_rsi_confirmation: boolean;
  require_kama_roc_confirmation: boolean;
  entry_volume_ratio: number;
  max_trade_lots: number;
  max_positions: number;
  min_cash_reserve: number;
  macd_invalidation_exit: boolean;
  macd_invalidation_max_mfe_pct: number;
  signal_mode: "zero_cross" | "signal_cross" | "both";
  auto_trade: boolean;
  order_quantity: number;
  slippage_bps: number;
  mp_max_positions: number;
  mp_max_trades_per_day: number;
  fyers_client_id: string;
  fyers_secret_configured: boolean;
  fyers_access_token_configured: boolean;
  fyers_redirect_uri: string;
  telegram_configured: boolean;
  telegram_chat_id: string;
  day_loss_alert_rupees: number;
  hard_stop_pct: number;
  trailing_stop_pct: number;
  broker: { status: string; error?: string; token_expires_at?: string | null; token_expired?: boolean; symbols?: string[] };
};

const headers = { "content-type": "application/json", ...(API_TOKEN ? { "x-macd-token": API_TOKEN } : {}) };

type PanelTab = "FYERS" | "UPSTOX" | "ALERTS" | "STRATEGY";
type LiveBroker = { status: string; error?: string | null; token_expires_at?: string | null; symbol_count?: number };

const IST_EXPIRY = new Intl.DateTimeFormat("en-IN", { timeZone: "Asia/Kolkata", weekday: "short", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: false });

export function SettingsPanel({ open, onClose, initialTab = "FYERS" }: { open: boolean; onClose: () => void; initialTab?: PanelTab }) {
  const panelRef = useRef<HTMLElement>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  const [settings, setSettings] = useState<RuntimeSettings>();
  const [panelTab, setPanelTab] = useState<PanelTab>(initialTab);
  const [live, setLive] = useState<LiveBroker>();
  const [clock, setClock] = useState(() => Date.now());
  const [symbols, setSymbols] = useState("");
  const [accessToken, setAccessToken] = useState("");
  const [appSecret, setAppSecret] = useState("");
  const [authInput, setAuthInput] = useState("");
  const [authUrl, setAuthUrl] = useState("");
  const [botToken, setBotToken] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [rebuilding, setRebuilding] = useState(false);
  const [acceptedTokenExpiry, setAcceptedTokenExpiry] = useState<string>();
  const [progress, setProgress] = useState("");

  useEffect(() => {
    if (!open) return;
    const previousFocus = document.activeElement;
    const panel = panelRef.current;
    const focusable = () => [...(panel?.querySelectorAll<HTMLElement>(
      'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
    ) ?? [])].filter((node) => node.getClientRects().length > 0);
    focusable()[0]?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") { event.preventDefault(); closeRef.current(); return; }
      if (event.key !== "Tab") return;
      const nodes = focusable();
      if (!nodes.length) { event.preventDefault(); return; }
      if (event.shiftKey && document.activeElement === nodes[0]) { event.preventDefault(); nodes[nodes.length - 1].focus(); }
      else if (!event.shiftKey && document.activeElement === nodes[nodes.length - 1]) { event.preventDefault(); nodes[0].focus(); }
    };
    const onFocus = (event: FocusEvent) => {
      if (event.target instanceof Node && !panel?.contains(event.target)) focusable()[0]?.focus();
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("focusin", onFocus);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("focusin", onFocus);
      if (previousFocus instanceof HTMLElement && previousFocus.isConnected) previousFocus.focus();
    };
  }, [open]);

  useEffect(() => { if (open) setPanelTab(initialTab); }, [open, initialTab]);

  // The status card reads the live broker state, not the settings snapshot.
  useEffect(() => {
    if (!open) return;
    let stopped = false;
    const poll = () => {
      setClock(Date.now());
      void fetch(`${API_URL}/health`).then((r) => r.json()).then((health) => {
        if (!stopped && health?.broker) setLive(health.broker as LiveBroker);
      }).catch(() => undefined);
    };
    poll();
    const timer = window.setInterval(poll, 5000);
    return () => { stopped = true; clearInterval(timer); };
  }, [open]);

  useEffect(() => {
    if (!open) return;
    setError("");
    fetch(`${API_URL}/api/settings`, { headers })
      .then(async (response) => {
        if (!response.ok) throw new Error(await responseErrorMessage(response, "Unable to load settings"));
        return response.json() as Promise<RuntimeSettings>;
      })
      .then((value) => { setSettings(value); setSymbols(value.symbols.join("\n")); })
      .catch((reason) => setError(reason instanceof Error ? reason.message : "Unable to load settings"));
  }, [open]);

  // Watch the rebuild without gating anything on it. Runs only while the panel
  // is open and a rebuild is in flight, and simply reports what it sees.
  useEffect(() => {
    if (!open || !rebuilding) return;
    let stopped = false;
    const poll = () => {
      void fetch(`${API_URL}/health`).then((r) => r.json()).then((health) => {
        if (stopped || !health?.broker) return;
        const currentExpiry = health.broker.token_expires_at || undefined;
        if (acceptedTokenExpiry && currentExpiry !== acceptedTokenExpiry) {
          setProgress("Waiting for the prior feed connection to close…");
          return;
        }
        setSettings((old) => old ? { ...old, broker: health.broker } : old);
        const count = (health.broker.symbols || []).length;
        if (health.broker.status === "connected") {
          setProgress(`Watchlist ready — ${count} instruments live.`);
          setAcceptedTokenExpiry(undefined);
          setRebuilding(false);
        } else if (health.broker.status === "error") {
          setProgress("");
          setAcceptedTokenExpiry(undefined);
          setRebuilding(false);
          setError(health.broker.error || "Fyers connection failed after the token was accepted");
        } else {
          setProgress(`Rebuilding… ${count} instruments so far.`);
        }
      }).catch(() => undefined);
    };
    poll();
    const timer = window.setInterval(poll, 3000);
    return () => { stopped = true; clearInterval(timer); };
  }, [open, rebuilding, acceptedTokenExpiry]);

  if (!open) return null;

  async function save() {
    if (!settings) return;
    setSaving(true); setError("");
    try {
      const payload = {
        feed_mode: settings.feed_mode,
        timeframe_seconds: Number(settings.timeframe_seconds),
        fast_period: Number(settings.fast_period),
        slow_period: Number(settings.slow_period),
        signal_period: Number(settings.signal_period),
        bb_period: Number(settings.bb_period),
        bb_deviations: Number(settings.bb_deviations),
        kama_period: Number(settings.kama_period),
        kama_fast: Number(settings.kama_fast),
        kama_slow: Number(settings.kama_slow),
        kama_rsi_period: Number(settings.kama_rsi_period),
        kama_rsi_min: Number(settings.kama_rsi_min),
        kama_roc_period: Number(settings.kama_roc_period),
        kama_roc_min: Number(settings.kama_roc_min),
        require_kama_confirmation: settings.require_kama_confirmation,
        require_kama_rsi_confirmation: settings.require_kama_rsi_confirmation,
        require_kama_roc_confirmation: settings.require_kama_roc_confirmation,
        entry_volume_ratio: Number(settings.entry_volume_ratio),
        signal_mode: settings.signal_mode,
        auto_trade: settings.auto_trade,
        order_quantity: Number(settings.order_quantity),
        slippage_bps: Number(settings.slippage_bps),
        max_positions: Number(settings.max_positions),
        min_cash_reserve: Number(settings.min_cash_reserve),
        macd_invalidation_exit: Boolean(settings.macd_invalidation_exit),
        macd_invalidation_max_mfe_pct: Number(settings.macd_invalidation_max_mfe_pct),
        mp_max_positions: Number(settings.mp_max_positions),
        mp_max_trades_per_day: Number(settings.mp_max_trades_per_day),
      };
      const response = await fetch(`${API_URL}/api/settings`, { method: "PUT", headers, body: JSON.stringify(payload) });
      if (!response.ok) throw new Error(await responseErrorMessage(response, "Settings rejected"));
      window.location.reload();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Settings could not be saved");
      setSaving(false);
    }
  }

  async function persistFyersCredentials() {
    if (!settings) throw new Error("Settings are still loading");
    const clientId = settings.fyers_client_id.trim();
    if (!clientId) throw new Error("Enter your Fyers client ID first.");
    if (!settings.fyers_secret_configured && !appSecret.trim()) {
      throw new Error("Enter your Fyers app secret first.");
    }
    const response = await fetch(`${API_URL}/api/auth/fyers/credentials`, {
      method: "POST", headers,
      body: JSON.stringify({ client_id: clientId, secret: appSecret.trim() || undefined, redirect_uri: settings.fyers_redirect_uri.trim() }),
    });
    if (!response.ok) throw new Error(await responseErrorMessage(response, "Could not save Fyers app details"));
  }

  async function saveFyersCredentials() {
    setSaving(true); setError(""); setMessage(""); setAuthUrl("");
    try {
      await persistFyersCredentials();
      setMessage("Fyers app details saved locally.");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not save Fyers app details");
    } finally { setSaving(false); }
  }

  async function openFyersLogin() {
    if (!settings?.fyers_client_id.trim()) { setError("Enter your Fyers client ID first."); return; }
    if (!settings.fyers_secret_configured && !appSecret.trim()) { setError("Enter your Fyers app secret first."); return; }

    // The browser only allows popups during the click's user activation. Open
    // the window now, before either network request yields control.
    const popup = window.open("about:blank", "fyers-login", "popup,width=720,height=760");
    if (popup) popup.opener = null;
    setSaving(true); setError(""); setMessage(""); setAuthUrl("");
    try {
      await persistFyersCredentials();
      const response = await fetch(`${API_URL}/api/auth/fyers/url`, { headers });
      if (!response.ok) throw new Error(await responseErrorMessage(response, "Could not create Fyers login URL"));
      const result: unknown = await response.json();
      const rawUrl = result && typeof result === "object" && "auth_url" in result ? result.auth_url : undefined;
      if (typeof rawUrl !== "string") throw new Error("Fyers did not return a login URL");
      const loginUrl = new URL(rawUrl);
      if (loginUrl.protocol !== "https:" ||
          (loginUrl.hostname !== "fyers.in" && !loginUrl.hostname.endsWith(".fyers.in"))) {
        throw new Error("Fyers returned an invalid login URL");
      }
      if (popup && !popup.closed) {
        popup.location.replace(loginUrl.href);
        setMessage("Log in on the Fyers window. When it lands on the redirect page, copy that page's full address into step 2.");
      } else {
        setAuthUrl(loginUrl.href);
        setError("Your browser blocked the Fyers login window. Use the login link below.");
      }
    } catch (reason) {
      popup?.close();
      setMessage("");
      setError(reason instanceof Error ? reason.message : "Could not open Fyers login");
    } finally { setSaving(false); }
  }

  async function saveAuxiliary(payload: Record<string, unknown>, confirmation: string) {
    setSaving(true); setError(""); setMessage("");
    try {
      const response = await fetch(`${API_URL}/api/settings`, { method: "PUT", headers, body: JSON.stringify(payload) });
      if (!response.ok) throw new Error(await responseErrorMessage(response, "Settings rejected"));
      const result = await response.json();
      setSettings(result as RuntimeSettings);
      setBotToken("");
      setMessage(confirmation);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Settings could not be saved"); }
    finally { setSaving(false); }
  }

  async function connectWith(path: string, body: object) {
    setSaving(true); setError(""); setMessage("Verifying the token with Fyers…");
    try {
      const response = await fetch(`${API_URL}${path}`, { method: "POST", headers, body: JSON.stringify(body) });
      if (!response.ok) throw new Error(await responseErrorMessage(response, "Fyers connection failed"));
      const result = await response.json();
      // The server validates the token against Fyers before responding. A 200
      // proves the token, but the existing feed may still be closing and
      // the watchlist may still be rebuilding. Keep that distinct from a live
      // websocket so the panel cannot show success beside the prior error.
      const acceptedExpiry = result.accepted_token_expires_at || undefined;
      setAcceptedTokenExpiry(acceptedExpiry);
      setSettings((old) => old ? {
        ...old,
        fyers_access_token_configured: true,
        broker: { ...old.broker, status: "connecting", error: undefined,
          token_expired: false, token_expires_at: acceptedExpiry },
      } : old);
      setMessage("Token verified and saved — the Fyers market-data feed is reconnecting. "
        + "This panel will confirm when the watchlist is live.");
      setAccessToken(""); setAuthInput("");
      setRebuilding(true);
    } catch (reason) {
      setMessage(""); setProgress("");
      setError(reason instanceof Error ? reason.message : "Fyers connection failed");
    }
    finally { setSaving(false); }
  }

  return <div className="modal-backdrop" role="presentation" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
    <section ref={panelRef} className="settings-panel" role="dialog" aria-modal="true" aria-label="Trading settings">
      <div className="settings-heading"><div><h2>Trading settings</h2><p>Real broker data · local paper execution</p></div><button aria-label="Close settings" onClick={onClose}>×</button></div>
      {!settings ? <div className="settings-loading">{error ? <><strong>Settings unavailable</strong><p>{error}</p><button onClick={() => window.location.reload()}>Retry</button></> : "Loading settings…"}</div> : <div className="settings-body">
        <div className="watch-tabs settings-tabs">{(["FYERS", "UPSTOX", "ALERTS", "STRATEGY"] as const).map((key) => <button key={key} className={panelTab === key ? "active" : ""} onClick={() => setPanelTab(key)}>{key === "FYERS" ? "Fyers login" : key === "UPSTOX" ? "Upstox live" : key === "ALERTS" ? "Alerts" : "Strategy"}</button>)}</div>
        {panelTab === "UPSTOX" && <UpstoxControls />}
        {panelTab === "FYERS" && <FyersLogin
          settings={settings} live={live} clock={clock} saving={saving} progress={progress} message={message} error={error}
          appSecret={appSecret} setAppSecret={setAppSecret} authInput={authInput} setAuthInput={setAuthInput}
          accessToken={accessToken} setAccessToken={setAccessToken} authUrl={authUrl}
          onSettings={setSettings} onOpenLogin={() => void openFyersLogin()} onSaveApp={() => void saveFyersCredentials()}
          onExchange={(code) => void connectWith("/api/auth/fyers/exchange", { auth_code: code })}
          onToken={() => void connectWith("/api/auth/fyers/access-token", { access_token: accessToken.trim() })} />}
        {panelTab === "ALERTS" && <div className="settings-section">
          <h3>Telegram alerts</h3>
          <div className="token-paste"><label>Bot token<input type="password" value={botToken} onChange={(e) => setBotToken(e.target.value)} placeholder={settings.telegram_configured ? "Saved — leave blank to keep" : "123456:ABC… from @BotFather"} /></label><button disabled={saving || (!botToken.trim() && !settings.telegram_chat_id)} onClick={() => void saveAuxiliary({ ...(botToken.trim() ? { telegram_bot_token: botToken.trim() } : {}), telegram_chat_id: settings.telegram_chat_id, day_loss_alert_rupees: Number(settings.day_loss_alert_rupees) || 0 }, "Alert settings saved.")}>Save alerts</button></div>
          <div className="settings-grid">
            <label>Chat ID<input value={settings.telegram_chat_id} onChange={(e) => setSettings({ ...settings, telegram_chat_id: e.target.value })} placeholder="Your chat id (message @userinfobot)" /></label>
            <label>Day-loss alert (₹, 0 = off)<input type="number" min="0" step="1000" value={settings.day_loss_alert_rupees} onChange={(e) => setSettings({ ...settings, day_loss_alert_rupees: Number(e.target.value) })} /></label>
          </div>
          <p className="field-help">Alerts: broker down or feed stale during market hours, day P&L breaching the level above, and event-loop saturation. Alert only — nothing is halted automatically. Status: {settings.telegram_configured ? "configured" : "not configured"}.</p>
          {message && <div className="settings-message">{message}</div>}{error && <div className="settings-error">{error}</div>}
        </div>}
        {panelTab === "STRATEGY" && <div className="settings-section">
          <h3>Strategy</h3>
          <div className="settings-grid three">
            <label>Timeframe<select value={settings.timeframe_seconds} onChange={(e) => setSettings({ ...settings, timeframe_seconds: Number(e.target.value) })}><option value={60}>1 minute</option><option value={180}>3 minutes</option><option value={300}>5 minutes</option><option value={900}>15 minutes</option><option value={1800}>30 minutes</option></select></label>
            <label>Fast EMA<input type="number" min="2" value={settings.fast_period} onChange={(e) => setSettings({ ...settings, fast_period: Number(e.target.value) })} /></label>
            <label>Slow EMA<input type="number" min="3" value={settings.slow_period} onChange={(e) => setSettings({ ...settings, slow_period: Number(e.target.value) })} /></label>
            <label>Signal EMA<input type="number" min="1" value={settings.signal_period} onChange={(e) => setSettings({ ...settings, signal_period: Number(e.target.value) })} /></label>
            <label>Entry trigger<input value="MACD ≤ 0 → > 0 (mandatory)" disabled /></label>
            <label>BB period<input type="number" min="2" value={settings.bb_period} onChange={(e) => setSettings({ ...settings, bb_period: Number(e.target.value) })} /></label>
            <label>BB deviations<input type="number" min="0.1" step="0.1" value={settings.bb_deviations} onChange={(e) => setSettings({ ...settings, bb_deviations: Number(e.target.value) })} /></label>
            <label>KAMA period<input type="number" min="1" value={settings.kama_period} onChange={(e) => setSettings({ ...settings, kama_period: Number(e.target.value) })} /></label>
            <label>KAMA fast<input type="number" min="1" value={settings.kama_fast} onChange={(e) => setSettings({ ...settings, kama_fast: Number(e.target.value) })} /></label>
            <label>KAMA slow<input type="number" min="2" value={settings.kama_slow} onChange={(e) => setSettings({ ...settings, kama_slow: Number(e.target.value) })} /></label>
            <label>KAMA RSI period<input type="number" min="2" value={settings.kama_rsi_period} onChange={(e) => setSettings({ ...settings, kama_rsi_period: Number(e.target.value) })} /></label>
            <label>KAMA RSI minimum<input type="number" min="0" max="100" step="1" disabled={!settings.require_kama_rsi_confirmation} value={settings.kama_rsi_min} onChange={(e) => setSettings({ ...settings, kama_rsi_min: Number(e.target.value) })} /></label>
            <label>KAMA ROC period<input type="number" min="1" value={settings.kama_roc_period} onChange={(e) => setSettings({ ...settings, kama_roc_period: Number(e.target.value) })} /></label>
            <label>KAMA ROC minimum %<input type="number" min="-100" max="100" step="0.1" disabled={!settings.require_kama_roc_confirmation} value={settings.kama_roc_min} onChange={(e) => setSettings({ ...settings, kama_roc_min: Number(e.target.value) })} /></label>
            <label>Volume filter<input value="Removed — shown as radar context only" disabled /></label>
            <label>Position sizing<input value={settings.max_trade_lots === 1 ? "1 lot · no pyramiding" : `1 initial lot · maximum ${settings.max_trade_lots} lots`} disabled /></label>
            <label>Scale-in levels<input value={settings.max_trade_lots === 1 ? "Disabled" : "+7.5%, +15%, +22.5%"} disabled /></label>
            <label>Scale-out levels<input value={settings.max_trade_lots === 1 ? "Disabled" : "+30%, +50%, +75%"} disabled /></label>
            <label>Hard stop<input value="30%" disabled /></label><label>Trailing activation<input value="After +30% profit" disabled /></label><label>Trailing stop<input value="25% from peak" disabled /></label>
          </div>
          <div className="confirmation-settings">
            <h3>Entry conditions</h3>
            <p className="field-help">MACD crossing upward from at or below zero to above zero is always required. A gap that jumps across zero is a valid cross. Enable any additional confirmations only when you want them to veto that entry.</p>
            <label className="toggle fixed"><input type="checkbox" checked readOnly disabled /><span>MACD cross up through zero — mandatory</span></label>
            <label className="toggle"><input type="checkbox" checked={settings.require_kama_confirmation} onChange={(e) => setSettings({ ...settings, require_kama_confirmation: e.target.checked })} /><span>Require premium above a rising KAMA</span></label>
            <label className="toggle"><input type="checkbox" checked={settings.require_kama_rsi_confirmation} onChange={(e) => setSettings({ ...settings, require_kama_rsi_confirmation: e.target.checked })} /><span>Require RSI(KAMA) ≥ {settings.kama_rsi_min}</span></label>
            <label className="toggle"><input type="checkbox" checked={settings.require_kama_roc_confirmation} onChange={(e) => setSettings({ ...settings, require_kama_roc_confirmation: e.target.checked })} /><span>Require ROC(KAMA) &gt; {settings.kama_roc_min}%</span></label>
          </div>
          <h3 style={{ marginTop: 18 }}>MACD portfolio limits</h3>
          <p>Limits apply to future buys and pending commitments. Existing positions remain held; sells remain available. Zero disables the additional limit.</p>
          <div className="settings-grid">
            <label>Max open positions (0 = unlimited)<input type="number" min="0" max="1000" value={settings.max_positions} onChange={(e) => setSettings({ ...settings, max_positions: Number(e.target.value) })} /></label>
            <label>Minimum cash reserve (₹)<input type="number" min="0" value={settings.min_cash_reserve} onChange={(e) => setSettings({ ...settings, min_cash_reserve: Number(e.target.value) })} /></label>
          </div>
          <h3 style={{ marginTop: 18 }}>Signal invalidation exit</h3>
          <p>The entry is a MACD zero-cross up, so MACD closing back below zero is that thesis failing. Measured 3–10 Sep, it did so before the −30% stop in 87% of stopped positions, a median 20.8h earlier. Acting on every dip loses money — it also cuts 29% of winners — so it only applies while a position has never been up by the margin below. Anything that has cleared that margin keeps the existing scale-out, trailing and hard-stop ladder untouched.</p>
          <label className="toggle"><input type="checkbox" checked={settings.macd_invalidation_exit} onChange={(e) => setSettings({ ...settings, macd_invalidation_exit: e.target.checked })} />Close unproven positions when MACD closes below zero</label>
          <div className="settings-grid">
            <label>Only while never up more than (fraction)<input type="number" step="0.01" min="0" max="1" value={settings.macd_invalidation_max_mfe_pct} onChange={(e) => setSettings({ ...settings, macd_invalidation_max_mfe_pct: Number(e.target.value) })} /></label>
          </div>
          <p className="field-help">0.10 = the rule applies only to positions that have never shown a 10% gain. In the sample that closed 56 losers for +₹4.82L and touched no winner; 0 disables it as surely as the checkbox.</p>
          <h3 style={{ marginTop: 18 }}>Market Profile desk limits</h3>
          <div className="settings-grid three">
            <label>Max open positions<input type="number" min="1" max="20" value={settings.mp_max_positions} onChange={(e) => setSettings({ ...settings, mp_max_positions: Number(e.target.value) })} /></label>
            <label>Max trades per day<input type="number" min="1" max="100" value={settings.mp_max_trades_per_day} onChange={(e) => setSettings({ ...settings, mp_max_trades_per_day: Number(e.target.value) })} /></label>
          </div>
          <p className="field-help">Risk limits on the Market Profile / Order Flow desk's own book, saved with the strategy. When that desk stops opening positions and its rejection line reads “max_positions reached”, this number is the constraint — not a shortage of signals.</p>
          <label className="toggle"><input type="checkbox" checked={settings.auto_trade} onChange={(e) => setSettings({ ...settings, auto_trade: e.target.checked })} /><span>Automatically place paper orders on MACD signals</span></label>
          {error && <div className="settings-error">{error}</div>}
          <div className="settings-actions"><button className="secondary" onClick={onClose}>Cancel</button><button className="primary" disabled={saving} onClick={() => void save()}>{saving ? "Saving…" : "Save strategy"}</button></div>
        </div>}
      </div>}
    </section>
  </div>;
}


type FyersLoginProps = {
  settings: RuntimeSettings; live?: LiveBroker; clock: number; saving: boolean; progress: string; message: string; error: string;
  appSecret: string; setAppSecret: (value: string) => void; authInput: string; setAuthInput: (value: string) => void;
  accessToken: string; setAccessToken: (value: string) => void; authUrl: string;
  onSettings: (value: RuntimeSettings) => void; onOpenLogin: () => void; onSaveApp: () => void;
  onExchange: (code: string) => void; onToken: () => void;
};

/** The morning routine first: status, then three steps; one-time app setup and the manual token are folded away. */
function FyersLogin(props: FyersLoginProps) {
  const { settings, live, clock, saving, progress, message, error } = props;
  const status = live?.status || settings.broker.status;
  const expiresAt = live?.token_expires_at ?? settings.broker.token_expires_at;
  const token = tokenLeft(expiresAt, new Date(clock));
  const connected = status === "connected" && !token.expired;
  const appReady = !!settings.fyers_client_id.trim() && settings.fyers_secret_configured;
  const code = extractAuthCode(props.authInput);
  const headline = connected ? "Connected to Fyers" : status === "connecting" ? "Connecting…"
    : token.expired && settings.fyers_access_token_configured ? "Today's login needed" : status === "stale" ? "Connected, but no ticks arriving" : "Not connected";
  const tone = connected ? "ok" : status === "connecting" ? "pending" : "down";
  return <div className="settings-section fyers-login">
    <div className={`login-status login-${tone}`}>
      <div className="login-headline"><i aria-hidden="true" /><div><b>{headline}</b>
        <small>{connected ? `Daily token valid until ${expiresAt ? IST_EXPIRY.format(new Date(expiresAt)) : "—"} IST · ${token.text}`
          : live?.error || settings.broker.error || "Fyers issues a new access token every day; log in once each morning."}</small></div></div>
      <dl>
        <div><dt>Client ID</dt><dd>{settings.fyers_client_id || "—"}</dd></div>
        <div><dt>Instruments live</dt><dd>{live?.symbol_count ?? "—"}</dd></div>
        <div><dt>Execution</dt><dd>Paper only</dd></div>
      </dl>
      {progress && <p className="login-progress">{progress}</p>}
    </div>

    {!appReady && <div className="settings-error">Set up the Fyers app details below once before the first login.</div>}
    <ol className={`login-steps${connected ? " done" : ""}`}>
      {connected && <p className="login-already">You're logged in for today. Log in again only if the feed fails or the token is revoked.</p>}
      <li>
        <div className="login-step-head"><span>1</span><b>Log in on Fyers</b></div>
        <p>Opens fyers.in in a popup. You sign in there — nothing sensitive is typed into this app.</p>
        <button className="primary" disabled={saving || !appReady} onClick={props.onOpenLogin}>Open Fyers login</button>
        {props.authUrl && <p className="field-help">Popup blocked: <a href={props.authUrl} target="_blank" rel="noopener noreferrer">open Fyers in a new tab</a></p>}
      </li>
      <li>
        <div className="login-step-head"><span>2</span><b>Paste the redirect address</b></div>
        <p>After login Fyers lands on a page whose address contains <code>auth_code</code>. Paste the whole address — or just the code.</p>
        <input className="login-input" value={props.authInput} onChange={(e) => props.setAuthInput(e.target.value)}
          placeholder="https://trade.fyers.in/api-login/redirect-uri/index.html?s=ok&auth_code=…" spellCheck={false} autoComplete="off" />
        {props.authInput.trim() && <small className={code ? "login-detect ok" : "login-detect bad"}>{code ? `✓ auth code found (${code.slice(0, 8)}…)` : "No auth_code in that text — copy the full address of the Fyers redirect page"}</small>}
      </li>
      <li>
        <div className="login-step-head"><span>3</span><b>Connect</b></div>
        <p>The code is exchanged for today's token on the server and the feed reconnects; this card turns green when the watchlist is live.</p>
        <button className="primary" disabled={saving || !code} onClick={() => code && props.onExchange(code)}>{saving ? "Connecting…" : "Connect Fyers"}</button>
      </li>
    </ol>
    {message && <div className="settings-message">{message}</div>}{error && <div className="settings-error">{error}</div>}

    <details className="login-more" open={!appReady}>
      <summary>Fyers app details <small>{appReady ? "saved" : "required once"}</small></summary>
      <div className="settings-grid">
        <label>Fyers client ID<input value={settings.fyers_client_id} onChange={(e) => props.onSettings({ ...settings, fyers_client_id: e.target.value })} placeholder="ABCD1234-100" /></label>
        <label>App secret<input type="password" value={props.appSecret} onChange={(e) => props.setAppSecret(e.target.value)} placeholder={settings.fyers_secret_configured ? "Saved — leave blank to keep" : "Fyers app secret"} /></label>
        <label className="wide">Redirect URL<input value={settings.fyers_redirect_uri} onChange={(e) => props.onSettings({ ...settings, fyers_redirect_uri: e.target.value })} /></label>
      </div>
      <div className="oauth-actions"><button className="secondary" disabled={saving} onClick={props.onSaveApp}>Save app details</button></div>
    </details>
    <details className="login-more">
      <summary>Paste an access token instead <small>advanced</small></summary>
      <div className="token-paste"><label>Daily access token<input type="password" value={props.accessToken} onChange={(e) => props.setAccessToken(e.target.value)} placeholder="Paste today's Fyers access token" /></label><button disabled={saving || !props.accessToken.trim()} onClick={props.onToken}>Connect</button></div>
      {/auth_code=|:\/\//.test(props.accessToken) && <small className="login-detect bad">This is the redirect address, not an access token — it will be exchanged as an auth code (step 2 does the same).</small>}
    </details>
    <p className="field-help">Tokens are stored only in the local Docker runtime volume and never returned to the browser. Automatic refresh is not used. Watchlist: 210 F&O stocks plus NIFTY, SENSEX, BANKNIFTY and MIDCPNIFTY, with ATM, adjacent and next-series index contracts.</p>
  </div>;
}

type UpstoxState = { armed: boolean; capital: number; free: number; deployed: number; realized: number;
  cap?: number | null; fund_basis?: string; armed_since?: string | null;
  settings?: { sides: "BOTH" | "CE" | "PE"; changed_at?: string | null; changed_by?: string | null };
  weekly_index_roots?: string[]; prefer_wait_seconds?: number;
  upstox_funds?: { available_margin?: number } | null; positions: { symbol: string; quantity: number }[];
  orders: { order_id: string; side: string; open?: boolean; dry?: boolean }[];
  order_api?: { ok: boolean; detail: string; checked_at: string } | null; read_error?: string | null; read_at?: string | null;
  orders_today?: { day: string; count: number }; max_orders_per_day?: number };

/** The Upstox live lane's owner switches (upstox_live/lane.py): arm real orders, kill, exit every lane position. */
function UpstoxControls() {
  const [lane, setLane] = useState<UpstoxState>();
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const r = await fetch("/api/upstox-live/snapshot", { cache: "no-store" });
        if (!r.ok) throw new Error(`Upstox lane service returned HTTP ${r.status}`);
        const body = await r.json();
        if (alive) { setLane(body); setError((old) => old.startsWith("Upstox lane service") ? "" : old); }
      } catch (e) { if (alive) setError(e instanceof Error ? e.message : String(e)); }
    };
    void load(); const id = window.setInterval(load, 5_000);
    return () => { alive = false; window.clearInterval(id); };
  }, []);
  const act = async (path: "arm" | "kill" | "exit-all" | "settings", body: Record<string, string>, done: (r: Record<string, unknown>) => string) => {
    setBusy(path); setMessage(""); setError("");
    try {
      const r = await fetch(`/api/upstox-live/${path}`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
      const result = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(result.detail || `HTTP ${r.status}`);
      if (result.snapshot) setLane(result.snapshot);
      setMessage(done(result));
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); } finally { setBusy(""); }
  };
  const [armText, setArmText] = useState("");
  const [exitAsk, setExitAsk] = useState(false);
  const arm = () => {
    if (armText.trim().toUpperCase() !== "ARM") { setError("Type ARM in the box first."); return; }
    void act("arm", { confirm: "ARM" }, () => "Armed: real orders from the next MACD signal, and it stays armed (across days and restarts) until you press Kill.").then(() => setArmText(""));
  };
  const kill = () => void act("kill", {}, (r) => `Killed: not armed, no new orders.${Array.isArray(r.cancelled_entries) && r.cancelled_entries.length ? ` Cancelled ${r.cancelled_entries.length} unfilled entry order(s).` : ""} Open positions are not sold — use Exit all.`);
  const exitAll = () => {
    if (!exitAsk) { setExitAsk(true); return; }
    setExitAsk(false);
    void act("exit-all", { confirm: "EXIT" }, (r) => `Exit orders sent for ${Array.isArray(r.sent) ? r.sent.length : 0} position(s)${Array.isArray(r.no_price) && r.no_price.length ? `; no Upstox price yet for ${r.no_price.join(", ")}` : ""}.`);
  };
  const sides = lane?.settings?.sides ?? "BOTH";
  const SIDE_LABEL = { BOTH: "Calls and puts", CE: "Calls only", PE: "Puts only" } as const;
  const setSides = (value: "BOTH" | "CE" | "PE") => {
    if (value === sides) return;
    void act("settings", { sides: value }, () => `Saved: the lane now trades ${SIDE_LABEL[value].toLowerCase()} from the next signal. Open positions are not affected.`);
  };
  const apiOk = !!lane?.order_api?.ok;
  const pendingEntries = (lane?.orders ?? []).filter((o) => o.side === "BUY" && o.open && !o.dry).length;
  const tone = !lane ? "pending" : lane.armed ? "down" : "ok";
  return <div className="settings-section fyers-login upstox-controls">
    <div className={`login-status ${tone}`}>
      <b>{!lane ? "Reading the Upstox lane…" : lane.armed ? `ARMED — real Upstox orders until Kill${lane.armed_since ? ` (since ${new Date(lane.armed_since).toLocaleString("en-IN", { timeZone: "Asia/Kolkata" })} IST)` : ""}` : "Not armed — no orders are sent"}</b>
      <span style={{ display: "block", fontWeight: 400 }}>{lane ? `Order API ${apiOk ? "ok" : "blocked"} · account read ${lane.read_error ? "failing" : "ok"} · ${lane.positions.length} open · ${pendingEntries} pending entr${pendingEntries === 1 ? "y" : "ies"} · orders today ${lane.orders_today?.count ?? 0}/${lane.max_orders_per_day ?? "—"}` : ""}</span>
    </div>
    {lane?.order_api && !apiOk && <div className="settings-error">Order API: {readableUpstreamError(lane.order_api.detail)}</div>}
    {lane?.read_error && <div className="settings-error">Account read: {lane.read_error}</div>}
    <h3>Switches</h3>
    <div className="settings-actions" style={{ justifyContent: "flex-start", flexWrap: "wrap" }}>
      {!lane?.armed && <input aria-label="Type ARM to confirm real orders" placeholder="type ARM" value={armText} onChange={(e) => setArmText(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Enter") arm(); }} style={{ width: 110 }} />}
      <button className="primary" disabled={!lane || lane.armed || !apiOk || !!busy || armText.trim().toUpperCase() !== "ARM"} title={apiOk ? "Send real orders from the next MACD signal (type ARM first)" : "The Upstox order API is blocked"} onClick={arm}>{busy === "arm" ? "Arming…" : lane?.armed ? "Armed" : "Arm real orders"}</button>
      <button className="secondary" style={{ borderColor: "var(--negative, #e5484d)", color: "var(--negative, #e5484d)" }} disabled={!lane || !!busy} title="Disarm at once and cancel unfilled entry orders" onClick={kill}>{busy === "kill" ? "Killing…" : "Kill (disarm)"}</button>
      <button className="secondary" disabled={!lane || !lane.positions.length || !!busy} title="Sell every lane position now" onClick={exitAll}>{busy === "exit-all" ? "Sending…" : exitAsk ? `Confirm: sell all ${lane?.positions.length ?? 0} now` : "Exit all lane positions"}</button>
      {exitAsk && <button className="secondary" onClick={() => setExitAsk(false)}>Cancel</button>}
    </div>
    <h3>Signals the lane trades</h3>
    <div className="settings-actions" role="radiogroup" aria-label="Option side" style={{ justifyContent: "flex-start", flexWrap: "wrap" }}>
      {(["BOTH", "CE", "PE"] as const).map((value) => <button key={value} role="radio" aria-checked={sides === value}
        className={sides === value ? "primary" : "secondary"} disabled={!lane || !!busy} onClick={() => setSides(value)}>{SIDE_LABEL[value]}</button>)}
    </div>
    <p className="field-help">
      {SIDE_LABEL[sides]}{sides !== "BOTH" ? ` — ${sides === "CE" ? "put" : "call"} signals are ignored and not shown in the lane` : ""}.
      Weekly-index signals ({(lane?.weekly_index_roots ?? ["NIFTY", "SENSEX"]).join(", ")}) get first preference: other signals wait {lane?.prefer_wait_seconds ?? 10} s, so a weekly-index signal arriving meanwhile is funded first.
      {lane?.settings?.changed_at ? ` Last changed ${new Date(lane.settings.changed_at).toLocaleString("en-IN", { timeZone: "Asia/Kolkata" })} IST.` : ""}
    </p>
    {message && <div className="settings-message">{message}</div>}{error && <div className="settings-error">{error}</div>}
    <p className="field-help">
      Free for the next entry ₹{Math.round(lane?.free ?? 0).toLocaleString("en-IN")} ({lane?.fund_basis ?? "—"}{lane?.cap ? `, cap ₹${lane.cap.toLocaleString("en-IN")}` : ", no lane cap"}) · Upstox available {lane?.upstox_funds?.available_margin == null ? "—" : `₹${Math.round(lane.upstox_funds.available_margin).toLocaleString("en-IN")}`}.
      The fund is the account's available margin, so a top-up is used within about 15 s. Arm once: real orders go out for every MACD option BUY signal whose one lot fits, and the lane stays armed across days, restarts and Upstox re-logins until Kill (an expired Upstox token only pauses entries). Exits follow the MACD lane. Kill stops new orders at once and cancels unfilled entries, but keeps open positions (their MACD exits are then not sent — use Exit all).
      Arming is refused while the order API is blocked. Only this Mac's own page can press these. Order API checked {lane?.order_api?.checked_at ? new Date(lane.order_api.checked_at).toLocaleString("en-IN", { timeZone: "Asia/Kolkata" }) + " IST" : "—"}.
    </p>
  </div>;
}
