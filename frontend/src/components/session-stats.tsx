import { useQuery } from "@tanstack/react-query";
import { Card, Skeleton } from "@/components/ui/card";
import { ErrorState } from "@/components/ui/error-state";
import { InfoTile } from "@/components/ui/info-tile";
import { fetchSessionUsage } from "@/lib/api";
import { errMsg, formatCost, formatTokens, formatUptime } from "@/lib/format";
import { useNowTick } from "@/lib/hooks/use-now-tick";
import { qk } from "@/lib/api/keys";

/** Session stats (since gateway start). Totals refetch every 10s——token 是内存读、成本是
 *  DB 查询,3s 过频;uptime ticks locally. */
export function SessionStats() {
  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: qk.sessionUsage,
    queryFn: fetchSessionUsage,
    refetchInterval: 10_000,
  });
  const now = useNowTick(1000);

  if (isError) return <ErrorState message={errMsg(error)} onRetry={() => refetch()} />;
  if (isLoading || !data) return <Skeleton rows={6} />;

  const pct = Math.round(data.hit_rate * 1000) / 10;  // 1 decimal place
  const uptimeSec = Math.max(0, Math.floor((now - data.started_at * 1000) / 1000));

  return (
    <Card>
      <div className="mb-3 flex items-baseline justify-between">
        <span className="text-sm font-semibold">本次启动</span>
        <span className="text-xs text-muted-foreground">运行 {formatUptime(uptimeSec)}</span>
      </div>
      {/* tile 更宽,2 位小数(与 kpi 行默认 1 位并存是有意的) */}
      <div className="grid grid-cols-2 gap-2">
        <InfoTile label="输入" value={formatTokens(data.input_tokens, 2)} />
        <InfoTile label="输出" value={formatTokens(data.output_tokens, 2)} />
        <InfoTile label="缓存命中" value={formatTokens(data.cache_hit, 2)} valueClass="text-success-accent" />
        <InfoTile label="缓存未命中" value={formatTokens(data.cache_miss, 2)} valueClass="text-destructive-accent" />
      </div>
      <div className="mt-2 rounded-lg border border-border-subtle bg-card-2 px-3 py-2">
        <div className="flex items-baseline justify-between">
          <span className="text-xs text-muted-foreground">命中率</span>
          <span className="font-semibold text-primary-accent">{pct}%</span>
        </div>
        <div className="mt-1.5 h-1.5 overflow-hidden rounded bg-destructive/25">
          <div className="h-full bg-success transition-[width] duration-(--motion-slow)" style={{ width: `${pct}%` }} />
        </div>
      </div>
      {/* 本次启动消耗:独立小卡(与命中率同款式)——后端 compute-on-read 窗口 [started_at, now) */}
      <div className="mt-2 rounded-lg border border-border-subtle bg-card-2 px-3 py-2">
        <div className="flex items-baseline justify-between">
          <span className="text-xs text-muted-foreground">本次启动消耗</span>
          <span className="font-semibold text-primary-accent">{formatCost(data.total_cost)}</span>
        </div>
      </div>
    </Card>
  );
}
