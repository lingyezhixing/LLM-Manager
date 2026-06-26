import { useQuery } from "@tanstack/react-query";
import { fetchSessionUsage } from "@/lib/api";

function fmt(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return `${n}`;
}

function Tile({ label, value, valueClass = "" }: { label: string; value: string; valueClass?: string }) {
  return (
    <div className="rounded-lg border border-border px-3 py-2">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className={`mt-0.5 text-base font-semibold ${valueClass}`}>{value}</div>
    </div>
  );
}

/** Session stats (since gateway start). Refetches /api/usage/session every 3s. */
export function SessionStats() {
  const { data, isLoading } = useQuery({
    queryKey: ["usage", "session"],
    queryFn: fetchSessionUsage,
    refetchInterval: 3000,
  });
  if (isLoading || !data) return <p className="text-sm text-muted-foreground">加载中…</p>;

  const pct = Math.round(data.hit_rate * 1000) / 10;  // 1 decimal place
  return (
    <div>
      <div className="mb-3 flex items-baseline justify-between">
        <span className="text-sm font-semibold">本次启动</span>
        <span className="text-xs text-muted-foreground">每 3s</span>
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
