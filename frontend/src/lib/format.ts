/** Number formatting helpers for the usage page (and reusable elsewhere).
 *  K/M suffix for tokens; ms/s for latency; % for rates/shares. */

export function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return `${n}`;
}

export function formatCount(n: number): string {
  return n.toLocaleString("en-US");
}

export function formatHitRate(r: number): string {
  return `${(r * 100).toFixed(1)}%`;
}

export function formatPercent(share: number): string {
  return `${(share * 100).toFixed(0)}%`;
}

export function formatLatency(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}
