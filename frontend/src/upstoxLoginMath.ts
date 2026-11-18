// Pure helpers for the Upstox login card (UpstoxLogin.tsx), kept free of the DOM so node --test can load them.

export type UpstoxLoginStatus = {
  status?: string; detail?: string;
  token_present?: boolean; token_expired?: boolean | null; token_expires_at?: string | null;
  trade_token_present?: boolean; trade_token_expired?: boolean | null; trade_token_expires_at?: string | null;
  fields?: Partial<Record<"api_key" | "secret" | "redirect_uri" | "analytics_token" | "access_token", boolean>>;
  redirect_uri?: string;
};

const IST = new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Kolkata", weekday: "short", day: "2-digit", month: "short", year: "numeric",
  hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23" });
export const istStamp = (iso?: string | null) => iso ? `${IST.format(new Date(iso))} IST` : "—";

/** The code Upstox appends to the registered redirect address (or the bare code / an eyJ… token pasted as is). */
export function upstoxCode(text: string): string {
  const value = text.trim();
  if (!value) return "";
  if (/^eyJ[\w-]+\.[\w-]+\.[\w-]+$/.test(value)) return value;
  if (value.includes("://") || value.includes("code=")) {
    try { return new URL(value.includes("://") ? value : `https://x/?${value.replace(/^\?/, "")}`).searchParams.get("code") || ""; }
    catch { return ""; }
  }
  return /^[\w-]{4,}$/.test(value) ? value : "";
}

/** What the card says: the order token is what the Upstox live lane needs each day. */
export function upstoxHeadline(s: UpstoxLoginStatus | undefined, now = Date.now()) {
  if (!s) return { tone: "pending", text: "Reading Upstox login…" };
  const tradeLive = !!s.trade_token_present && s.trade_token_expired === false
    && (!s.trade_token_expires_at || new Date(s.trade_token_expires_at).getTime() > now);
  const appReady = !!(s.fields?.api_key && s.fields?.secret && s.fields?.redirect_uri);
  if (tradeLive) return { tone: "ok", text: "Logged in to Upstox for today" };
  if (!appReady) return { tone: "down", text: "Upstox app details needed once" };
  return { tone: "down", text: s.trade_token_present ? "Today's Upstox login needed" : "Not logged in to Upstox" };
}
