"""Verify the Fyers trade-by-trade socket before anything depends on it.

The ordinary SymbolUpdate feed is a snapshot stream: it carries a cumulative
volume, so a burst of trades between two updates collapses into one number and
the individual prints are gone. The TBT socket is a different feed whose
protobuf envelope carries per-update volume (``vtt_diff``), last traded
quantity (``ltq``), a ``sequence_no`` and 50 levels of depth.

The SDK's own wrapper throws most of that away: ``DataStore.updateDepth`` calls
``Depth._addDepth``, which reads only ``MarketFeed.depth`` and discards
``MarketFeed.quote``. This probe substitutes its own decoder for that datastore,
so the SDK is used purely for connection, auth and reconnect plumbing.

It answers the two questions worth answering before trusting the feed:

  1. Are sequence numbers contiguous per symbol, or are updates being dropped?
  2. Does the sum of vtt_diff reconcile with the cumulative vtt?

Run it during market hours:

    docker exec -i macdmini-api-1 python /app/scripts/tbt_probe.py --seconds 120
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import sys
import threading
import time

RUNTIME = pathlib.Path("/app/runtime")
if not RUNTIME.exists():
    RUNTIME = pathlib.Path(__file__).resolve().parents[1] / "runtime"

TEST_UNIVERSE = [
    "NSE:NIFTY50-INDEX", "NSE:NIFTYBANK-INDEX", "BSE:SENSEX-INDEX",
    "NSE:ICICIBANK-EQ", "NSE:BSE-EQ",
]
SYMBOLS_PER_CHANNEL = 5


def _value(message, field):
    """Read a protobuf wrapper field, or None when it was not sent."""
    try:
        if not message.HasField(field):
            return None
    except ValueError:
        return None
    inner = getattr(message, field)
    return getattr(inner, "value", inner)


class Observation:
    __slots__ = ("ticker", "seq", "ltp", "ltq", "vtt", "vtt_diff", "oi",
                 "bid", "ask", "bid_qty", "ask_qty", "snapshot", "feed_time")

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))


class TbtDecoder:
    """Stands in for the SDK's DataStore and keeps the whole MarketFeed."""

    def __init__(self, sink):
        self.sink = sink
        self.depth = {}          # the SDK touches this attribute

    def updateDepth(self, packet, cb, diffOnly):  # noqa: N802 — SDK's spelling
        for _, feed in packet.feeds.items():
            try:
                self.sink(self._decode(feed, packet.snapshot))
            except Exception as exc:  # noqa: BLE001
                print(f"  decode error: {exc}", file=sys.stderr)

    @staticmethod
    def _decode(feed, snapshot) -> Observation:
        obs = Observation(ticker=feed.ticker, seq=feed.sequence_no, snapshot=snapshot,
                          feed_time=_value(feed, "feed_time"))
        if feed.HasField("quote"):
            q = feed.quote
            ltp = _value(q, "ltp")
            obs.ltp = ltp / 100 if ltp is not None else None
            obs.ltq = _value(q, "ltq")
            obs.vtt = _value(q, "vtt")
            obs.vtt_diff = _value(q, "vtt_diff")
            obs.oi = _value(q, "oi")
        if feed.HasField("depth"):
            d = feed.depth
            if len(d.bids):
                obs.bid = (_value(d.bids[0], "price") or 0) / 100
                obs.bid_qty = _value(d.bids[0], "qty")
            if len(d.asks):
                obs.ask = (_value(d.asks[0], "price") or 0) / 100
                obs.ask_qty = _value(d.asks[0], "qty")
        return obs


class Probe:
    def __init__(self):
        self.lock = threading.Lock()
        self.updates = 0
        self.with_quote = 0
        self.with_vtt_diff = 0
        self.with_ltq = 0
        self.with_depth = 0
        self.snapshots = 0
        self.per_symbol = collections.defaultdict(lambda: {
            "updates": 0, "seq": [], "vtt_diff_sum": 0, "vtt_last": None,
            "vtt_first": None, "ltq_sum": 0, "quotes": 0,
        })

    def observe(self, obs: Observation) -> None:
        with self.lock:
            self.updates += 1
            row = self.per_symbol[obs.ticker]
            row["updates"] += 1
            if obs.seq is not None:
                row["seq"].append(obs.seq)
            if obs.snapshot:
                self.snapshots += 1
            if obs.bid is not None or obs.ask is not None:
                self.with_depth += 1
            if obs.vtt is not None or obs.ltq is not None or obs.ltp is not None:
                self.with_quote += 1
                row["quotes"] += 1
            if obs.vtt_diff is not None:
                self.with_vtt_diff += 1
                row["vtt_diff_sum"] += obs.vtt_diff
            if obs.ltq is not None:
                self.with_ltq += 1
                row["ltq_sum"] += obs.ltq
            if obs.vtt is not None:
                if row["vtt_first"] is None:
                    row["vtt_first"] = obs.vtt
                row["vtt_last"] = obs.vtt

    def report(self) -> int:
        print("\n" + "=" * 72)
        print(f"  updates received       : {self.updates:,}")
        print(f"  carrying a quote block : {self.with_quote:,}")
        print(f"  carrying vtt_diff      : {self.with_vtt_diff:,}")
        print(f"  carrying ltq           : {self.with_ltq:,}")
        print(f"  carrying depth         : {self.with_depth:,}")
        print(f"  snapshot packets       : {self.snapshots:,}")
        if not self.updates:
            print("\n  VERDICT: no data. Outside market hours, or the subscription failed.")
            return 1
        if not self.with_quote:
            print("\n  VERDICT: depth only -- no quote block on any update.")
            print("  ltq/vtt/vtt_diff are defined in the protobuf but this")
            print("  subscription mode does not deliver them. TBT cannot supply")
            print("  per-trade volume here; the regular feed is no worse.")
            return 2

        print("\n  per symbol:")
        print(f"    {'symbol':<24}{'upd':>7}{'quotes':>8}{'seq gaps':>10}"
              f"{'sum(vtt_diff)':>15}{'vtt delta':>12}{'match':>7}")
        ok = True
        for ticker, row in sorted(self.per_symbol.items()):
            seq = sorted(row["seq"])
            gaps = sum(1 for a, b in zip(seq, seq[1:]) if b - a > 1)
            delta = (row["vtt_last"] - row["vtt_first"]
                     if row["vtt_last"] is not None and row["vtt_first"] is not None else None)
            summed = row["vtt_diff_sum"]
            # The first update establishes the vtt baseline, so its own
            # vtt_diff is not part of the delta being reconciled.
            match = "n/a"
            if delta is not None and summed:
                drift = abs(summed - delta) / max(1, delta)
                match = "yes" if drift <= 0.02 else f"{drift:.1%}"
                if drift > 0.02:
                    ok = False
            if gaps:
                ok = False
            print(f"    {ticker:<24}{row['updates']:>7}{row['quotes']:>8}{gaps:>10}"
                  f"{summed:>15,}{(delta if delta is not None else 0):>12,}{match:>7}")

        print("\n  VERDICT:", "usable -- sequences contiguous and vtt_diff reconciles"
              if ok else "SUSPECT -- see gaps / mismatches above")
        return 0 if ok else 3


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=int, default=60)
    parser.add_argument("--symbols", default=None,
                        help="comma separated; defaults to the 5 spot test universe")
    args = parser.parse_args()

    creds = json.loads((RUNTIME / "credentials.json").read_text())
    token = f"{creds['fyers_client_id']}:{creds['fyers_access_token']}"
    symbols = ([s.strip() for s in args.symbols.split(",") if s.strip()]
               if args.symbols else list(TEST_UNIVERSE))

    from fyers_apiv3.FyersWebsocket.tbt_ws import FyersTbtSocket, SubscriptionModes

    probe = Probe()
    ready = threading.Event()

    def on_open():
        ready.set()

    socket = FyersTbtSocket(
        access_token=token, write_to_file=False, log_path=None,
        on_open=on_open, on_error=lambda m: print(f"  socket error: {m}", file=sys.stderr),
        on_error_message=lambda m: print(f"  server error: {m}", file=sys.stderr),
        reconnect=False,
    )
    # Substitute our decoder for the SDK's depth-only datastore.
    socket._datastore = TbtDecoder(probe.observe)

    print(f"  connecting; {len(symbols)} symbols, {SYMBOLS_PER_CHANNEL} per channel")
    threading.Thread(target=socket.connect, daemon=True).start()
    if not ready.wait(timeout=20):
        print("  VERDICT: socket never opened (auth or network).")
        return 1
    time.sleep(1)

    for index in range(0, len(symbols), SYMBOLS_PER_CHANNEL):
        chunk = symbols[index:index + SYMBOLS_PER_CHANNEL]
        channel = str(index // SYMBOLS_PER_CHANNEL + 1)
        socket.subscribe(set(chunk), channel, SubscriptionModes.DEPTH)
        print(f"    channel {channel}: {chunk}")
        time.sleep(0.5)

    print(f"  listening for {args.seconds}s ...")
    time.sleep(args.seconds)
    with_suppress = getattr(socket, "close_connection", lambda: None)
    try:
        with_suppress()
    except Exception:  # noqa: BLE001
        pass
    return probe.report()


if __name__ == "__main__":
    sys.exit(main())
