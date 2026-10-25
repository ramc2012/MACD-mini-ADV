"use client";

/**
 * Quote-tape microstructure pulse.
 *
 * This intentionally does not replace completed-bar CVD/footprints. It layers
 * the shared /ws/quotes stream on top of them so traders can see the current
 * bid/ask skew and signed quote/volume changes between strategy scans.
 */
import { useEffect, useMemo, useRef, useState } from "react";

import { StatusBadge, formatNumber } from "@/components/desk-ui";
import { QuoteConnectionBadge } from "@/components/terminal/LiveMarkBadge";
import { useKnownSymbols, useQuote, type Quote } from "@/hooks/useQuoteStore";

type Pulse = {
  ts: number;
  price: number;
  delta: number;
  cumulative: number;
  imbalance: number | null;
  volumeBacked: boolean;
};

function token(value: string): string {
  return value
    .toUpperCase()
    .replace(/^NSE:|^BSE:|^MCX:/, "")
    .replace(/50-INDEX|-INDEX|INDEX/g, "")
    .replace(/[^A-Z0-9]/g, "");
}

function resolveSymbol(requested: string | null | undefined, known: string[]): string | null {
  if (!requested) return null;
  if (known.includes(requested)) return requested;
  const wanted = token(requested);
  const exact = known.find((candidate) => token(candidate) === wanted);
  if (exact) return exact;
  return (
    known
      .filter((candidate) => {
        const candidateToken = token(candidate);
        return candidateToken.startsWith(wanted) || wanted.startsWith(candidateToken);
      })
      .sort(
        (left, right) =>
          Math.abs(token(left).length - wanted.length) -
          Math.abs(token(right).length - wanted.length),
      )[0] ?? requested
  );
}

function quoteSide(quote: Quote, previousPrice: number | null): -1 | 0 | 1 {
  const price = quote.ltp;
  if (price == null) return 0;
  if (quote.ask != null && price >= quote.ask) return 1;
  if (quote.bid != null && price <= quote.bid) return -1;
  if (previousPrice != null) return price > previousPrice ? 1 : price < previousPrice ? -1 : 0;
  const bidQty = Number(quote.bidQty ?? 0);
  const askQty = Number(quote.askQty ?? 0);
  return bidQty > askQty ? 1 : askQty > bidQty ? -1 : 0;
}

function sparkPath(values: number[], width = 520, height = 96): string {
  if (values.length < 2) return "";
  const min = Math.min(0, ...values);
  const max = Math.max(0, ...values);
  const span = max - min || 1;
  return values
    .map((value, index) => {
      const x = (index / (values.length - 1)) * width;
      const y = height - ((value - min) / span) * height;
      return `${index === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
}

export function LiveOrderFlowTape({
  symbol,
  title = "Live quote-tape pulse",
}: {
  symbol?: string | null;
  title?: string;
}) {
  const known = useKnownSymbols();
  const resolved = useMemo(() => resolveSymbol(symbol, known), [known, symbol]);
  const quote = useQuote(resolved);
  const [pulses, setPulses] = useState<Pulse[]>([]);
  const previous = useRef<{ ts: number; price: number | null; volume: number | null }>({
    ts: 0,
    price: null,
    volume: null,
  });

  useEffect(() => {
    setPulses([]);
    previous.current = { ts: 0, price: null, volume: null };
  }, [resolved]);

  useEffect(() => {
    if (!quote || quote.ltp == null || quote.ts === previous.current.ts) return;
    const side = quoteSide(quote, previous.current.price);
    const rawVolumeDelta =
      quote.volume != null && previous.current.volume != null
        ? quote.volume - previous.current.volume
        : 0;
    const volumeDelta = rawVolumeDelta > 0 ? rawVolumeDelta : 0;
    const signedDelta = side * (volumeDelta || (side === 0 ? 0 : 1));
    const depth = Number(quote.bidQty ?? 0) + Number(quote.askQty ?? 0);
    const imbalance =
      depth > 0 ? (Number(quote.bidQty ?? 0) - Number(quote.askQty ?? 0)) / depth : null;

    setPulses((current) => {
      const cumulative = (current.at(-1)?.cumulative ?? 0) + signedDelta;
      return [
        ...current,
        {
          ts: quote.ts,
          price: quote.ltp as number,
          delta: signedDelta,
          cumulative,
          imbalance,
          volumeBacked: volumeDelta > 0,
        },
      ].slice(-120);
    });
    previous.current = { ts: quote.ts, price: quote.ltp, volume: quote.volume ?? null };
  }, [quote]);

  const latest = pulses.at(-1);
  const path = sparkPath(pulses.map((pulse) => pulse.cumulative));
  const bookImbalance = latest?.imbalance ?? null;
  const volumeBacked = pulses.some((pulse) => pulse.volumeBacked);
  const net = latest?.cumulative ?? 0;
  const netTone = net > 0 ? "success" : net < 0 ? "error" : "neutral";
  const bookTone =
    bookImbalance == null
      ? "neutral"
      : bookImbalance > 0.12
        ? "success"
        : bookImbalance < -0.12
          ? "error"
          : "neutral";

  return (
    <div className="rounded-xl border border-bg-border bg-bg-primary/20 p-3">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div>
          <div className="text-sm font-semibold text-text-primary">{title}</div>
          <div className="mt-0.5 font-mono text-[10px] text-text-muted">{resolved ?? symbol ?? "no symbol"}</div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <QuoteConnectionBadge />
          <StatusBadge
            label={volumeBacked ? "volume delta" : "tick-rule proxy"}
            variant={volumeBacked ? "success" : "warn"}
          />
        </div>
      </div>

      <div className="grid grid-cols-2 gap-2 md:grid-cols-5">
        <TapeMetric label="LTP" value={formatNumber(quote?.ltp, 2)} />
        <TapeMetric label="Bid / Ask" value={`${formatNumber(quote?.bid, 2)} / ${formatNumber(quote?.ask, 2)}`} />
        <TapeMetric
          label="Book skew"
          value={bookImbalance == null ? "—" : `${bookImbalance >= 0 ? "+" : ""}${formatNumber(bookImbalance * 100, 1)}%`}
          variant={bookTone}
        />
        <TapeMetric label="Rolling Δ" value={`${net >= 0 ? "+" : ""}${formatNumber(net, 0)}`} variant={netTone} />
        <TapeMetric label="Pulses" value={String(pulses.length)} />
      </div>

      <div className="mt-3 h-24 overflow-hidden rounded-lg border border-bg-border/60 bg-bg-card/30">
        {path ? (
          <svg viewBox="0 0 520 96" preserveAspectRatio="none" className="h-full w-full" role="img" aria-label="Rolling signed order-flow pulse">
            <line x1="0" x2="520" y1="48" y2="48" stroke="rgba(148,163,184,.2)" strokeDasharray="4 4" />
            <path d={path} fill="none" stroke={net >= 0 ? "#00d4a3" : "#ff4757"} strokeWidth="2" vectorEffect="non-scaling-stroke" />
          </svg>
        ) : (
          <div className="flex h-full items-center justify-center text-[11px] text-text-muted">
            Waiting for the first live quote changes…
          </div>
        )}
      </div>

      <div className="mt-2 text-[10px] leading-4 text-text-muted">
        Live layer only. Strategy signals, CVD and footprints remain completed-bar values; this pulse uses cumulative volume when the feed supplies it, otherwise a signed tick-rule proxy.
      </div>
    </div>
  );
}

function TapeMetric({
  label,
  value,
  variant = "neutral",
}: {
  label: string;
  value: string;
  variant?: "neutral" | "success" | "error";
}) {
  const color =
    variant === "success"
      ? "text-accent-green"
      : variant === "error"
        ? "text-accent-red"
        : "text-text-primary";
  return (
    <div className="rounded-lg border border-bg-border/60 bg-bg-card/30 px-2.5 py-2">
      <div className="text-[9px] uppercase tracking-[0.14em] text-text-muted">{label}</div>
      <div className={`mt-1 truncate font-mono text-[12px] font-semibold ${color}`}>{value}</div>
    </div>
  );
}
