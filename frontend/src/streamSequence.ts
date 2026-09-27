export function createSequenceGate() {
  let last: number | undefined;
  let waiting = true;
  return (event: { type: string; seq?: number }): "accept" | "request" | "skip" => {
    if (event.type === "snapshot") {
      last = event.seq;
      waiting = false;
      return "accept";
    }
    // Unsequenced replies to this client, not book events.
    if (event.type === "pong" || event.type === "order_error") return "skip";
    if (waiting) return "skip";
    if (event.type === "snapshot_required" || (last !== undefined && event.seq !== undefined && event.seq > last + 1)) {
      waiting = true;
      return "request";
    }
    if (event.seq !== undefined && last !== undefined && event.seq <= last) return "skip";
    last = event.seq;
    return "accept";
  };
}
