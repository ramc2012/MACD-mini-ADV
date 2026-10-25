/**
 * Canonical formatters. Replaces the 8 hand-rolled copies of these
 * helpers scattered across the v1 workspaces.
 *
 * All formatters tolerate null / undefined / NaN and return a stable
 * placeholder ("—" by default) so tables don't break on partial data.
 */

const NA = "—";

export function formatMoney(value?: number | null, digits = 0): string {
  if (value == null || Number.isNaN(value)) return NA;
  return `₹${value.toLocaleString("en-IN", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })}`;
}

export function formatSignedMoney(value?: number | null, digits = 0): string {
  if (value == null || Number.isNaN(value)) return NA;
  const prefix = value > 0 ? "+" : "";
  return `${prefix}₹${value.toLocaleString("en-IN", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })}`;
}

export function formatNumber(value?: number | null, digits = 2): string {
  if (value == null || Number.isNaN(value)) return NA;
  return value.toFixed(digits);
}

export function formatSignedNumber(value?: number | null, digits = 2): string {
  if (value == null || Number.isNaN(value)) return NA;
  const prefix = value > 0 ? "+" : "";
  return `${prefix}${value.toFixed(digits)}`;
}

/**
 * Percent formatter. Treats `value` as a RATIO by default (0.05 → 5.00%).
 * Pass `asPercent: true` if your input is already a percent (5 → 5.00%).
 */
export function formatPct(
  value?: number | null,
  digits = 2,
  { asPercent = false }: { asPercent?: boolean } = {},
): string {
  if (value == null || Number.isNaN(value)) return NA;
  const pct = asPercent ? value : value * 100;
  return `${pct.toFixed(digits)}%`;
}

/**
 * Parse a backend timestamp safely. The backend emits TZ-NAIVE ISO strings
 * (offset stripped) that are actually UTC; with no designator the browser parses
 * them as LOCAL time, shifting IST desks by +5:30 — trades looked like "odd
 * hours" and the freshness pill read permanently "stale". Treat a naive datetime
 * as UTC; leave already-tz-aware, date-only, numeric and Date inputs untouched.
 */
export function toDate(value?: string | number | Date | null): Date {
  if (value == null) return new Date(NaN);
  if (value instanceof Date) return value;
  if (typeof value === "number") return new Date(value);
  let s = String(value).trim();
  const hasTz = /[zZ]$|[+-]\d{2}:?\d{2}$/.test(s);
  const isDateTime = /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}/.test(s);
  if (isDateTime && !hasTz) s = s.replace(" ", "T") + "Z";
  return new Date(s);
}

/**
 * IST timestamp formatter — DD MMM HH:mm.
 * Replaces ad-hoc `new Date(iso).toLocaleString("en-IN", …)` calls.
 */
export function formatIST(value?: string | number | Date | null): string{if(value==null)return NA;const date=toDate(value);if(!Number.isFinite(date.getTime()))return NA;const parts=Object.fromEntries(new Intl.DateTimeFormat('en-GB',{timeZone:'Asia/Kolkata',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'}).formatToParts(date).map(p=>[p.type,p.value]));return parts.year+'-'+parts.month+'-'+parts.day+' '+parts.hour+':'+parts.minute+':'+parts.second+' IST';}

/** Clock-only IST timestamp — HH:mm, with optional seconds for chart tooltips. */
export function formatISTTime(
  value?: string | number | Date | null,
  { seconds = false }: { seconds?: boolean } = {},
): string{return formatIST(value);}

/** Full timestamp incl. year — for audit feeds, journal rows. */
export function formatTimestamp(value?: string | number | Date | null): string{return formatIST(value);}

/** Seconds → "Nh Mm Ss" or "Mm Ss" or "Ss". For data-age badges. */
export function formatDuration(seconds?: number | null): string {
  if (seconds == null || Number.isNaN(seconds) || seconds < 0) return NA;
  const s = Math.floor(seconds);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const rem_s = s % 60;
  if (m < 60) return `${m}m ${rem_s}s`;
  const h = Math.floor(m / 60);
  const rem_m = m % 60;
  return `${h}h ${rem_m}m`;
}
