// Upstox login inside the app's settings, beside the Fyers login. The market-intelligence console owns the Upstox
// credentials and tokens (console/upstox_auth.py); the frontend's web server forwards /mi/api/logins* to it, so this
// card talks to the same endpoints as MI → Platform → Brokers. Nothing sensitive is typed here except the redirect
// address Upstox hands back after the owner signs in on upstox.com; no token is ever returned to the browser.
import { useCallback, useEffect, useState } from "react";
import { MI_URL } from "./runtime";

import { istStamp, upstoxCode, upstoxHeadline, type UpstoxLoginStatus } from "./upstoxLoginMath";

export function UpstoxLogin() {
  const [state, setState] = useState<UpstoxLoginStatus>();
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [pasted, setPasted] = useState("");
  const [busy, setBusy] = useState(false);
  const load = useCallback(async () => {
    try {
      const r = await fetch("/mi/api/logins", { cache: "no-store" });
      if (!r.ok) throw new Error(`Market-intelligence console returned HTTP ${r.status}`);
      const body = await r.json();
      setState(body.upstox || {}); setError((old) => old.startsWith("Market-intelligence console") || old.startsWith("Failed") ? "" : old);
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
  }, []);
  useEffect(() => { void load(); const id = window.setInterval(load, 30_000); return () => window.clearInterval(id); }, [load]);

  const code = upstoxCode(pasted);
  const appReady = !!(state?.fields?.api_key && state?.fields?.secret && state?.fields?.redirect_uri);
  const head = upstoxHeadline(state);
  const connect = async () => {
    if (!code) return;
    setBusy(true); setMessage(""); setError("");
    try {
      const r = await fetch("/mi/api/logins/upstox", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ pasted: pasted.trim() }) });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(body.detail || `HTTP ${r.status}`);
      setPasted(""); setMessage("Upstox login saved — the Upstox live lane uses it for orders from now until it expires.");
      await load();
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); }
  };
  const brokers = `${MI_URL}/?section=Platform&sub=Brokers`;
  return <div className="settings-section fyers-login upstox-login">
    <div className={`login-status login-${head.tone}`}>
      <div className="login-headline"><i aria-hidden="true" /><div><b>{head.text}</b>
        <small>{state?.trade_token_expires_at ? `Order token valid until ${istStamp(state.trade_token_expires_at)}` : "Upstox issues a new order token every day; log in once each morning before arming the Upstox live lane."}</small></div></div>
      <dl>
        <div><dt>Order token</dt><dd>{!state ? "—" : !state.trade_token_present ? "none" : state.trade_token_expired ? "expired" : "valid"}</dd></div>
        <div><dt>Market data</dt><dd>{!state ? "—" : state.token_expired ? "expired" : state.token_present ? `until ${state.token_expires_at ? istStamp(state.token_expires_at).slice(0, 16) : "—"}` : "none"}</dd></div>
        <div><dt>Managed by</dt><dd>MI console</dd></div>
      </dl>
    </div>
    {state && !appReady && <div className="settings-error">Save the Upstox API key, secret and redirect URI once in <a href={brokers} target="_blank" rel="noopener noreferrer">MI → Platform → Brokers ↗</a>, then log in here.</div>}
    <ol className="login-steps">
      <li>
        <div className="login-step-head"><span>1</span><b>Log in on Upstox</b></div>
        <p>Opens upstox.com in a new tab. You sign in there — nothing sensitive is typed into this app.</p>
        {appReady ? <a className="button primary" href="/mi/api/logins/upstox/open" target="_blank" rel="noopener noreferrer">Open Upstox login</a>
          : <button className="primary" disabled>Open Upstox login</button>}
      </li>
      <li>
        <div className="login-step-head"><span>2</span><b>Paste the redirect address</b></div>
        <p>After login Upstox lands on your registered redirect{state?.redirect_uri ? <> (<code>{state.redirect_uri}</code>)</> : ""} with <code>code=</code> in the address. Paste the whole address within 15 minutes.</p>
        <input className="login-input" type="password" value={pasted} onChange={(e) => setPasted(e.target.value)} placeholder="https://…?code=…&state=…" spellCheck={false} autoComplete="off" />
        {pasted.trim() && <small className={code ? "login-detect ok" : "login-detect bad"}>{code ? "✓ code found" : "No code= in that text — copy the full address of the Upstox redirect page"}</small>}
      </li>
      <li>
        <div className="login-step-head"><span>3</span><b>Connect</b></div>
        <p>The console exchanges the code for today's token on the server; this card turns green when it is saved.</p>
        <button className="primary" disabled={busy || !code} onClick={() => void connect()}>{busy ? "Connecting…" : "Connect Upstox"}</button>
      </li>
    </ol>
    {message && <div className="settings-message">{message}</div>}{error && <div className="settings-error">{error}</div>}
    <p className="field-help">Same login as MI → Platform → Brokers: credentials stay encrypted in the market-intelligence console and are never shown again. Arming real orders stays under the Upstox live tab.</p>
  </div>;
}
