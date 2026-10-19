// Market-session clock for the header, and helpers for the daily Fyers login.
// Pure functions of (now, holidays) so the header and the tests agree.

export type SessionPhase = "preopen" | "open" | "closed" | "holiday";
export type SessionState = { phase: SessionPhase; label: string; detail: string; minutesToNext: number | null };

const IST_OFFSET_MIN = 330;
const PREOPEN = 9 * 60;          // 09:00
const OPEN = 9 * 60 + 15;        // 09:15
const CLOSE = 15 * 60 + 30;      // 15:30 (F&O runs to 15:40; the index session ends here)
const WEEKDAY = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

function istParts(now: Date) {
  const shifted = new Date(now.getTime() + IST_OFFSET_MIN * 60_000);
  return { day: shifted.toISOString().slice(0, 10), weekday: shifted.getUTCDay(), minutes: shifted.getUTCHours() * 60 + shifted.getUTCMinutes() };
}

function isTradingDay(day: string, weekday: number, holidays: Set<string>) {
  return weekday !== 0 && weekday !== 6 && !holidays.has(day);
}

export function duration(minutes: number): string {
  const m = Math.max(0, Math.round(minutes));
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  return h >= 24 ? `${Math.floor(h / 24)}d ${h % 24}h` : `${h}h ${String(m % 60).padStart(2, "0")}m`;
}

/** Where the Indian cash/F&O session stands at ``now``, with the countdown to the next boundary. */
export function sessionState(now: Date, holidays: string[] = []): SessionState {
  const set = new Set(holidays);
  const { day, weekday, minutes } = istParts(now);
  const trading = isTradingDay(day, weekday, set);
  if (trading && minutes >= OPEN && minutes < CLOSE) {
    return { phase: "open", label: "Market open", detail: `closes in ${duration(CLOSE - minutes)}`, minutesToNext: CLOSE - minutes };
  }
  if (trading && minutes >= PREOPEN && minutes < OPEN) {
    return { phase: "preopen", label: "Pre-open", detail: `opens in ${duration(OPEN - minutes)}`, minutesToNext: OPEN - minutes };
  }
  // Closed: find the next trading day's 09:15.
  let ahead = trading && minutes < PREOPEN ? 0 : 1;
  for (; ahead < 15; ahead++) {
    const probe = istParts(new Date(now.getTime() + ahead * 86_400_000));
    if (isTradingDay(probe.day, probe.weekday, set)) {
      const wait = ahead * 1440 + OPEN - minutes;
      const when = ahead === 0 ? "today" : ahead === 1 ? "tomorrow" : WEEKDAY[probe.weekday];
      const phase: SessionPhase = !trading && set.has(day) ? "holiday" : "closed";
      return { phase, label: phase === "holiday" ? "Holiday" : "Market closed",
        detail: wait < 12 * 60 ? `opens in ${duration(wait)}` : `opens ${when} 09:15`, minutesToNext: wait };
    }
  }
  return { phase: "closed", label: "Market closed", detail: "", minutesToNext: null };
}

/** The ``auth_code`` from whatever the user pasted: the bare code, or the whole Fyers redirect URL. */
export function extractAuthCode(pasted: string): string | null {
  const text = pasted.trim();
  if (!text) return null;
  const fromQuery = (query: string) => new URLSearchParams(query).get("auth_code");
  if (/^https?:\/\//i.test(text)) {
    try {
      const url = new URL(text);
      return fromQuery(url.search) || fromQuery(url.hash.replace(/^#/, "")) || null;
    } catch { return null; }
  }
  if (text.includes("auth_code=")) return fromQuery(text.slice(text.indexOf("?") + 1)) || fromQuery(text);
  // A bare code: a JWT-like token with no spaces.
  return /^[\w.-]{20,}$/.test(text) ? text : null;
}

/** "22h 15m left" / "expired" for the daily token. */
export function tokenLeft(expiresAt: string | null | undefined, now: Date): { text: string; expired: boolean; minutes: number | null } {
  if (!expiresAt) return { text: "no token", expired: true, minutes: null };
  const minutes = (new Date(expiresAt).getTime() - now.getTime()) / 60_000;
  if (!Number.isFinite(minutes)) return { text: "unknown", expired: false, minutes: null };
  return minutes <= 0 ? { text: "expired", expired: true, minutes } : { text: `${duration(minutes)} left`, expired: false, minutes };
}
