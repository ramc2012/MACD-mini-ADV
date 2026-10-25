"use client";

/**
 * Price-flash: returns "up" | "down" for ~160ms whenever `value` changes, then
 * null. Drives the green/red cell pulse on a live quote — the visual signature
 * of a terminal. Direction is computed from the actual value delta, not a timer.
 */
import { useEffect, useRef, useState } from "react";

export function usePriceFlash(value?: number | null, durationMs = 160): "up" | "down" | null {
  const prev = useRef<number | null | undefined>(value);
  const [flash, setFlash] = useState<"up" | "down" | null>(null);

  useEffect(() => {
    const p = prev.current;
    if (value != null && p != null && value !== p) {
      const next = value > p ? "up" : "down";
      setFlash(next);
      prev.current = value;
      const t = setTimeout(() => setFlash(null), durationMs);
      return () => clearTimeout(t);
    }
    prev.current = value;
  }, [value, durationMs]);

  return flash;
}
