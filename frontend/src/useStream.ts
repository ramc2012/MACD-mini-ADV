import { useEffect, useRef, useState } from "react";
import type { Candle, StreamEvent } from "./types";
import { API_TOKEN, WS_URL } from "./runtime";
import { createSequenceGate } from "./streamSequence";

export function useStream(onEvent: (event: StreamEvent) => void) {
  const [connected, setConnected] = useState(false);
  const callback = useRef(onEvent);
  callback.current = onEvent;

  useEffect(() => {
    let socket: WebSocket | undefined;
    let stopped = false;
    let retry = 0;
    let timer: number | undefined;
    let tickTimer: number | undefined;
    let tickSeq = 0;
    let resyncTimer: number | undefined;
    const pendingTicks = new Map<string, unknown>();
    // Newest forming bar per symbol — plus the bar before it when a rollover
    // lands inside one flush window, so the closed bar's final state is never
    // coalesced away.
    const pendingCandles = new Map<string, Candle[]>();
    const clearPending = () => {
      if (tickTimer !== undefined) clearTimeout(tickTimer);
      tickTimer = undefined;
      pendingTicks.clear();
      pendingCandles.clear();
    };
    const flushTicks = () => {
      tickTimer = undefined;
      if (pendingTicks.size) {
        const rows = [...pendingTicks.values()];
        pendingTicks.clear();
        callback.current({ seq: tickSeq, type: "tick_batch", data: rows });
      }
      if (pendingCandles.size) {
        const queued = [...pendingCandles.values()];
        pendingCandles.clear();
        // A bar superseded inside one flush window has closed: the engine
        // publishes only the forming bar, so that snapshot is the last and
        // only sight of its final state.  React collapses two setCurrent
        // calls made in one task into the newer bar, so the closed rows
        // cannot ride `current` -- they travel as their own event for the
        // history merge, and only the newest bar per symbol stays `current`.
        const closed = queued.flatMap((rows) => rows.filter((row, index) => index < rows.length - 1 || row.closed));
        if (closed.length) callback.current({ seq: tickSeq, type: "candle_batch", data: closed });
        queued.forEach((rows) => callback.current({ seq: tickSeq, type: "candle", data: rows[rows.length - 1] }));
      }
    };
    const connect = () => {
      clearPending();
      const gate = createSequenceGate();
      const url = new URL(WS_URL);
      if (API_TOKEN) url.searchParams.set("token", API_TOKEN);
      socket = new WebSocket(url);
      socket.onopen = () => {
        resyncTimer = window.setTimeout(() => socket?.close(), 10_000);
      };
      socket.onmessage = (message) => {
        try {
          const event = JSON.parse(message.data) as StreamEvent;
          const decision = gate(event);
          if (decision === "request") {
            clearPending();
            setConnected(false);
            socket?.send(JSON.stringify({ command: "snapshot" }));
            resyncTimer = window.setTimeout(() => socket?.close(), 10_000);
            return;
          }
          if (decision === "skip") return;
          if (event.type === "snapshot") {
            clearPending();
            clearTimeout(resyncTimer);
            retry = 0;
            setConnected(true);
          }
          if (event.type === "tick") {
            const row = event.data as { symbol?: string };
            if (row.symbol) pendingTicks.set(row.symbol, event.data);
            tickSeq = event.seq;
            // Quotes arrive at several hundred frames per second. The display
            // needs the newest quote per symbol, not every intermediate print;
            // a 10 Hz commit keeps the tape feeling live without rerendering
            // hundreds of watch rows for every websocket message.
            if (tickTimer === undefined) tickTimer = window.setTimeout(flushTicks, 100);
            return;
          }
          if (event.type === "candle") {
            // The forming bar is published on every tick of its symbol; it
            // rides the same 10 Hz commit as the quotes so the chart is not
            // re-rendered per websocket frame.
            const row = event.data as Candle;
            if (row.symbol) {
              const queued = pendingCandles.get(row.symbol) || [];
              if (queued.length && queued[queued.length - 1].timestamp === row.timestamp) queued[queued.length - 1] = row;
              else queued.push(row);
              pendingCandles.set(row.symbol, queued);
            }
            tickSeq = event.seq;
            if (tickTimer === undefined) tickTimer = window.setTimeout(flushTicks, 100);
            return;
          }
          callback.current(event);
        } catch { /* malformed frame */ }
      };
      socket.onclose = () => {
        clearPending();
        clearTimeout(resyncTimer);
        setConnected(false);
        if (!stopped) timer = window.setTimeout(connect, Math.min(30_000, 500 * 2 ** retry++));
      };
    };
    connect();
    return () => { stopped = true; if (timer) clearTimeout(timer); clearTimeout(resyncTimer); clearPending(); socket?.close(); };
  }, []);
  return connected;
}
