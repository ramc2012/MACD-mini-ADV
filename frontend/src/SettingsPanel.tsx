import { useEffect, useRef, useState } from "react";
import { responseErrorMessage } from "./apiError";
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

// "connecting" is a transient state, not a failure — styling it as an error
// put a red box and a green box on screen at the same time.
function brokerTone(status: string) {
  if (status === "connected") return "settings-message";
  if (status === "error" || status === "token_expired" || status === "stale") return "settings-error";
  return "settings-pending";
}

function brokerLabel(status: string) {
  if (status === "token_expired") return "daily token expired — reconnect below";
  if (status === "stale") return "connected but not receiving ticks";
  if (status === "connecting") return "connecting…";
  return status;
}

const headers = { "content-type": "application/json", ...(API_TOKEN ? { "x-macd-token": API_TOKEN } : {}) };

export function SettingsPanel({ open, onClose }: { open: boolean; onClose: () => void }) {
  const panelRef = useRef<HTMLElement>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  const [settings, setSettings] = useState<RuntimeSettings>();
  const [panelTab, setPanelTab] = useState<"BROKER" | "STRATEGY">("BROKER");
  const [symbols, setSymbols] = useState("");
  const [accessToken, setAccessToken] = useState("");
  const [appSecret, setAppSecret] = useState("");
  const [authCode, setAuthCode] = useState("");
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
        setMessage("Complete login in the Fyers window. If it ends on the Fyers redirect page, copy auth_code from its URL and paste it below.");
      } else {
        setAuthUrl(loginUrl.href);
        setError("Your browser blocked the Fyers login window. Use the login link below.");
      }
    } catch (reason) {
      popup?.close();
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
      setAccessToken(""); setAuthCode("");
      setRebuilding(true);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Fyers connection failed"); }
    finally { setSaving(false); }
  }

  return <div className="modal-backdrop" role="presentation" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
    <section ref={panelRef} className="settings-panel" role="dialog" aria-modal="true" aria-label="Trading settings">
      <div className="settings-heading"><div><h2>Trading settings</h2><p>Real broker data · local paper execution</p></div><button aria-label="Close settings" onClick={onClose}>×</button></div>
      {!settings ? <div className="settings-loading">{error ? <><strong>Settings unavailable</strong><p>{error}</p><button onClick={() => window.location.reload()}>Retry</button></> : "Loading settings…"}</div> : <div className="settings-body">
        <div className="watch-tabs settings-tabs">{(["BROKER", "STRATEGY"] as const).map((key) => <button key={key} className={panelTab === key ? "active" : ""} onClick={() => setPanelTab(key)}>{key === "BROKER" ? "Broker connection" : "Strategy"}</button>)}</div>
        {panelTab === "BROKER" && <div className="settings-section">
          <h3>Fyers settings</h3>
          <div className="settings-grid">
            <label>Feed<select value="fyers" disabled><option value="fyers">Fyers live data</option></select></label>
            <label>Execution<input value="PAPER ONLY" disabled /></label>
            <label>Fyers client ID<input value={settings.fyers_client_id} onChange={(e) => setSettings({ ...settings, fyers_client_id: e.target.value })} placeholder="ABCD1234-100" /></label>
            <label>App secret<input type="password" value={appSecret} onChange={(e) => setAppSecret(e.target.value)} placeholder={settings.fyers_secret_configured ? "Saved — leave blank to keep" : "Fyers app secret"} /></label>
            <label className="wide">Redirect URL<input value={settings.fyers_redirect_uri} onChange={(e) => setSettings({ ...settings, fyers_redirect_uri: e.target.value })} /></label>
          </div>
          <div className="oauth-actions"><button className="primary" disabled={saving} onClick={() => void openFyersLogin()}>Open Fyers login</button><button className="secondary" disabled={saving} onClick={() => void saveFyersCredentials()}>Save app details</button></div>
          {authUrl && <p className="field-help">Login link: <a href={authUrl} target="_blank" rel="noopener noreferrer">Open Fyers in a new tab</a> · <a href={authUrl}>Use this tab</a></p>}
          <div className="token-paste"><label>Authorization code<input value={authCode} onChange={(e) => setAuthCode(e.target.value)} placeholder="Paste auth_code from the Fyers redirect URL" /></label><button disabled={saving || !authCode.trim()} onClick={() => void connectWith("/api/auth/fyers/exchange", { auth_code: authCode.trim() })}>Exchange code</button></div>
          <div className="token-paste"><label>Daily access token<input type="password" value={accessToken} onChange={(e) => setAccessToken(e.target.value)} placeholder={settings.fyers_access_token_configured ? "Paste today’s replacement token" : "Paste today’s Fyers access token"} /></label><button className="connect-button" disabled={saving || !accessToken.trim()} onClick={() => void connectWith("/api/auth/fyers/access-token", { access_token: accessToken.trim() })}>Connect Fyers</button></div>
          <p className="field-help">Fyers requires a fresh daily access token. The token is stored only in the local Docker runtime volume and is never returned to the browser. Automatic refresh is not used.</p>
          <div className={brokerTone(settings.broker.status)}>
            Broker: {brokerLabel(settings.broker.status)}
            {settings.broker.status === "error" && settings.broker.error ? ` — ${settings.broker.error}` : ""}
            {" · daily token "}{settings.fyers_access_token_configured ? "saved" : "not saved"}
            {settings.broker.token_expires_at ? ` · expires ${new Date(settings.broker.token_expires_at).toLocaleString("en-IN", { timeZone: "Asia/Kolkata", hour: "2-digit", minute: "2-digit", day: "2-digit", month: "short", hour12: false })} IST` : ""}
            {progress ? ` · ${progress}` : ""}
          </div>
          <h3 style={{ marginTop: 18 }}>Telegram alerts</h3>
          <div className="token-paste"><label>Bot token<input type="password" value={botToken} onChange={(e) => setBotToken(e.target.value)} placeholder={settings.telegram_configured ? "Saved — leave blank to keep" : "123456:ABC… from @BotFather"} /></label><button disabled={saving || (!botToken.trim() && !settings.telegram_chat_id)} onClick={() => void saveAuxiliary({ ...(botToken.trim() ? { telegram_bot_token: botToken.trim() } : {}), telegram_chat_id: settings.telegram_chat_id, day_loss_alert_rupees: Number(settings.day_loss_alert_rupees) || 0 }, "Alert settings saved.")}>Save alerts</button></div>
          <div className="settings-grid">
            <label>Chat ID<input value={settings.telegram_chat_id} onChange={(e) => setSettings({ ...settings, telegram_chat_id: e.target.value })} placeholder="Your chat id (message @userinfobot)" /></label>
            <label>Day-loss alert (₹, 0 = off)<input type="number" min="0" step="1000" value={settings.day_loss_alert_rupees} onChange={(e) => setSettings({ ...settings, day_loss_alert_rupees: Number(e.target.value) })} /></label>
          </div>
          <p className="field-help">Alerts: broker down or feed stale during market hours, day P&L breaching the level above, and event-loop saturation. Alert only — nothing is halted automatically. Status: {settings.telegram_configured ? "configured" : "not configured"}.</p>
          {message && <div className="settings-message">{message}</div>}{error && <div className="settings-error">{error}</div>}
          <p className="field-help">Watchlist: 210 F&O stocks plus NIFTY, SENSEX, BANKNIFTY and MIDCPNIFTY. The app selects ATM CE and PE contracts for trading and adjacent strikes for analysis. The stock list follows the FYERS NSE F&O instrument master dated 25 September 2026.</p>
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
