// Market-watch tabs. Options are split by strike distance: ATM, and ATM+1 --
// one listed strike out of the money (the next strike above ATM for a call,
// below it for a put), which is the ladder's OTM leg. ITM legs stay on the
// ratio chart and are not listed in the watch.
export type WatchTab = "SPOT" | "CE_ATM" | "CE_ATM1" | "PE_ATM" | "PE_ATM1";

export const WATCH_TABS: { key: WatchTab; label: string }[] = [
  { key: "SPOT", label: "SPOT" },
  { key: "CE_ATM", label: "CE ATM" },
  { key: "CE_ATM1", label: "CE ATM+1" },
  { key: "PE_ATM", label: "PE ATM" },
  { key: "PE_ATM1", label: "PE ATM+1" },
];

const OPTION_TABS: Record<Exclude<WatchTab, "SPOT">, { side: "CE" | "PE"; moneyness: "ATM" | "OTM" }> = {
  CE_ATM: { side: "CE", moneyness: "ATM" },
  CE_ATM1: { side: "CE", moneyness: "OTM" },
  PE_ATM: { side: "PE", moneyness: "ATM" },
  PE_ATM1: { side: "PE", moneyness: "OTM" },
};

type OptionRow = { option_type: string; moneyness?: string | null };

export function optionRowsForTab<T extends OptionRow>(rows: T[], tab: WatchTab): T[] {
  if (tab === "SPOT") return [];
  const { side, moneyness } = OPTION_TABS[tab];
  // Rows from older snapshots carry no moneyness; they were ATM-only.
  return rows.filter((row) => row.option_type === side && (row.moneyness || "ATM") === moneyness);
}
