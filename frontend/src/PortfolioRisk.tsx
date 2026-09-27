import { useMemo, useState } from "react";
import type { OptionWatchRow, Portfolio, Snapshot } from "./types";

const money = (value: number) => `₹${value.toLocaleString("en-IN", { maximumFractionDigits: 2 })}`;
export function PortfolioRisk({ portfolio, contracts, limits }: { portfolio: Portfolio; contracts: OptionWatchRow[]; limits?: Snapshot["execution"]["risk"] }) {
  const [group, setGroup] = useState<"underlying" | "sector" | "option_type" | "expiry">("underlying");
  const groups = useMemo(() => {
    const metadata = new Map(contracts.map((row) => [row.symbol, row]));
    const values = new Map<string, { value: number; count: number }>();
    portfolio.positions.forEach((row) => {
      const label = metadata.get(row.symbol)?.[group] || "Metadata unavailable";
      const current = values.get(label) || { value: 0, count: 0 };
      current.value += Math.abs(row.quantity * row.last_price);
      current.count++;
      values.set(label, current);
    });
    return [...values].sort((a, b) => b[1].value - a[1].value);
  }, [portfolio.positions, contracts, group]);
  return <details className="portfolio-risk">
    <summary>Portfolio exposure · Cash {money(portfolio.cash)} · {portfolio.positions.length} positions · Position limit {limits?.max_positions || "off"} · Cash reserve {money(limits?.min_cash_reserve || 0)}</summary>
    <p>Gross option premium value at the latest stored marks; values can be stale when the feed is unavailable. This is not underlying or delta-adjusted exposure.</p>
    <div className="risk-tabs">{(["underlying", "sector", "option_type", "expiry"] as const).map((key) => <button key={key} onClick={() => setGroup(key)} aria-pressed={group === key}>{key === "option_type" ? "CE / PE" : key}</button>)}</div>
    <div className="risk-table"><table className="ledger-table"><thead><tr><th>Group</th><th>Positions</th><th>Premium value</th><th>% of equity</th></tr></thead><tbody>{groups.map(([name, row]) => <tr key={name}><td>{name}</td><td>{row.count}</td><td>{money(row.value)}</td><td>{portfolio.equity > 0 ? (100 * row.value / portfolio.equity).toFixed(1) + "%" : "—"}</td></tr>)}</tbody></table></div>
  </details>;
}
