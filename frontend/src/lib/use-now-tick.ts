import { useEffect, useState } from "react";

/**
 * Ticks `now` (epoch ms) every `intervalMs` so time-derived displays (uptime, idle) update
 * locally without a refetch — the backend ships wall-clock timestamps once and the client
 * computes the elapsed value each tick.
 */
export function useNowTick(intervalMs = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
  return now;
}
