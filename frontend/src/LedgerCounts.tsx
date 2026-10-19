import type { Order, Trade } from "./types";
import { ledgerCounts } from "./ledgerCounts";

export function LedgerCounts({orders, trades, ready}: {orders: Order[]; trades: Trade[]; ready: boolean}) {
  const counts = ledgerCounts(orders,trades);
  return <section className="panel ledger-counts" aria-label="Order and fill reconciliation">
    <div className="ledger-count-cards">
      {[["Order attempts",counts.orders],["Booked / filled",counts.booked],["Rejected",counts.rejected],
        ["Other order states",counts.other],["Trade fills",counts.trades],["Buy fills",counts.buys],["Sell fills",counts.sells]].map(([label,value]) =>
        <div key={label}><small>{label}</small><b>{ready ? Number(value).toLocaleString("en-IN") : "…"}</b></div>)}
    </div>
    <p role="status">{!ready ? "Loading the complete ledger; counts are not final yet." :
      counts.unlinked || counts.notBooked || counts.withoutFill
        ? `Reconciliation needs review: ${counts.unlinked} fills without an order, ${counts.notBooked} fills linked to an unbooked order, ${counts.withoutFill} booked orders without fills.`
        : "Every trade fill links to a booked order. Orders include rejected attempts; trades count executed fills, including partial exits."}</p>
  </section>;
}
