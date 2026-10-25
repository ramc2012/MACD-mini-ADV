/** Shared recharts palette — matches the desk-ui Tailwind theme (globals.css). */
export const CHART = {
  green: "#00d4a3",
  red: "#ff4757",
  amber: "#ffa502",
  blue: "#3b82f6",
  violet: "#a78bfa",
  pink: "#f472b6",
  blueSoft: "rgba(59,130,246,0.55)",
  blueFaint: "rgba(59,130,246,0.07)",
  barMuted: "rgba(255,255,255,0.18)",
  text: "#e6edf3",
  grid: "rgba(255,255,255,0.06)",
  axis: "rgba(255,255,255,0.40)",
  muted: "rgba(255,255,255,0.55)",
  surface: "#0d1117",
  border: "rgba(255,255,255,0.12)",
} as const;

export const pnlColor = (v?: number | null): string =>
  v == null ? CHART.muted : v > 0 ? CHART.green : v < 0 ? CHART.red : CHART.blue;
