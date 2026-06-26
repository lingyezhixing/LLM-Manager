import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { fetchSessionUsage } from "@/lib/api";

function fmt(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return `${n}`;
}

/** Compact uptime: 45s / 12m / 3h 12m / 2d 5h. */
function formatUptime(sec: number): string {
  if (sec < 60) return `${sec}s`;
  const m = Math.floor(sec / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ${m % 60}m`;
  const d = Math.floor(h / 24);
  return `${d}d ${h % 24}h`;
}

/** Ticks `now` every interval so time-derived displays update locally (no refetch). */
function useNowTick(intervalMs = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
  return now;
}

function Tile({ label, value, valueClass = "" }: { label: string; value: string; valueClass?: string }) {
  return (
    <div className="rounded-lg border border-border px-3 py-2">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className={`mt-0.5 text-base font-semibold ${valueClass}`}>{value}</div>
    </div>
  );
}

/** Session stats (since gateway start). Totals refetch every 3s; uptime ticks locally. */
export function SessionStats() {
  const { data, isLoading } = useQuery({
    queryKey: ["usage", "session"],
    queryFn: fetchSessionUsage,
    refetchInterval: 3000,
  });
  const now = useNowTick(1000);

  if (isLoading || !data) return <p className="text-sm text-muted-foreground">加载中…</p>;

  const pct = Math.round(data.hit_rate * 1000) / 10;  // 1 decimal place
  const uptimeSec = Math.max(0, Math.floor((now - data.started_at * 1000) / 1000));

  return (
    <div>
      <div className="mb-3 flex items-baseline justify-between">
        <span className="text-sm font-semibold">本次启动</span>
        <span className="text-xs text-muted-foreground">运行 {formatUptime(uptimeSec)}</span>
      </div>
      <div className="grid grid-cols-2 gap-2">
        <Tile label="输入" value={fmt(data.input_tokens)} />
        <Tile label="输出" value={fmt(data.output_tokens)} />
        <Tile label="缓存命中" value={fmt(data.cache_hit)} valueClass="text-success" />
        <Tile label="缓存未命中" value={fmt(data.cache_miss)} valueClass="text-destructive" />
      </div>
      <div className="mt-2 rounded-lg border border-border px-3 py-2">
        <div className="flex items-baseline justify-between">
          <span className="text-xs text-muted-foreground">命中率</span>
          <span className="font-semibold text-primary">{pct}%</span>
        </div>
        <div className="mt-1.5 h-1.5 overflow-hidden rounded bg-destructive/25">
          <div className="h-full bg-success transition-[width] duration-300" style={{ width: `${pct}%` }} />
        </div>
      </div>
    </div>
  );
}
