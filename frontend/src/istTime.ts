/** IST has no daylight-saving changes. Avoid locale-dependent date ordering. */
export function istDay(at: number | string | Date = Date.now()): string {
  return new Date(new Date(at).getTime() + 19_800_000).toISOString().slice(0, 10);
}

export function istStamp(at?: string | null): string {
  if (!at) return "—";
  const time = Date.parse(at);
  if (!Number.isFinite(time)) return "—";
  return `${new Date(time + 19_800_000).toISOString().slice(0, 19).replace("T", " ")} IST`;
}
