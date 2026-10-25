// The market-intelligence platform's live workstation (TPO, footprint, volume profile, order book, tape, key levels,
// cumulative delta; streaming from the hub), embedded here so this app shows the same chart as the platform.
export function PlatformWorkstation() {
  const base = `${window.location.protocol}//${window.location.hostname}:8410`;
  return (
    <section className="ledger-page">
      <div className="page-heading"><div><h1>Live workstation</h1><p>From the market-intelligence platform · streams live ticks · <a href={`${base}/`} target="_blank" rel="noopener noreferrer">open the full platform</a></p></div></div>
      <iframe title="Live workstation" src={`${base}/?embed=workstation`} style={{ width: "100%", height: "calc(100vh - 170px)", minHeight: 640, border: 0, borderRadius: 10 }} />
    </section>
  );
}
