import { useQuery } from "@tanstack/react-query";
import { Card, Skeleton } from "@/components/ui/card";
import { ErrorState } from "@/components/ui/error-state";
import { InfoTile } from "@/components/ui/info-tile";
import { fetchSessionUsage } from "@/lib/api";
import { errMsg, formatCost, formatTokens, formatUptime } from "@/lib/format";
import { useNowTick } from "@/lib/hooks/use-now-tick";
import { qk } from "@/lib/api/keys";

/** 会话统计(自网关启动起)。总额每 10s 重新拉取——token 是内存读、成本是
 *  DB 查询,3s 过频;uptime 本地 tick。 */
export function SessionStats() {
  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: qk.sessionUsage,
    queryFn: fetchSessionUsage,
    refetchInterval: 10_000,
  });
  const now = useNowTick(1000);

  if (isError) return <ErrorState message={errMsg(error)} onRetry={() => refetch()} />;
  if (isLoading || !data) return <Skeleton rows={6} />;

  const pct = Math.round(data.hit_rate * 1000) / 10;  // 保留 1 位小数
  const uptimeSec = Math.max(0, Math.floor((now - data.started_at * 1000) / 1000));
  // 消耗占比条:总额为分母(0 时两项恒 0,只剩轨道)
  const totalCost = Math.max(0, data.total_cost);
  const localPct = totalCost > 0 ? (Math.max(0, data.local_cost) / totalCost) * 100 : 0;
  const cloudPct = totalCost > 0 ? (Math.max(0, data.cloud_cost) / totalCost) * 100 : 0;

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
          <span className="font-mono font-semibold text-primary-accent tabular-nums">{pct}%</span>
        </div>
        <div className="mt-1.5 h-1.5 overflow-hidden rounded bg-destructive/25">
          <div className="h-full bg-success transition-[width] duration-(--motion-slow)" style={{ width: `${pct}%` }} />
        </div>
      </div>
      {/* 本次启动消耗:独立小卡(与命中率同款式)——后端 compute-on-read 窗口 [started_at, now)。
          总额为主数;本地/云端拆分用同色异透明度占比条 + 对齐图例(圆点色与条段一一对应)。 */}
      <div className="mt-2 rounded-lg border border-border-subtle bg-card-2 px-3 py-2">
        <div className="flex items-baseline justify-between">
          <span className="text-xs text-muted-foreground">本次启动消耗</span>
          <span className="font-mono text-base font-semibold tabular-nums text-primary-accent">
            {formatCost(data.total_cost)}
          </span>
        </div>
        {/* 占比条按金额份额分两段;总额为 0 时两段宽度 0 → 只剩轨道 */}
        <div className="mt-2 flex h-1.5 overflow-hidden rounded-full bg-muted">
          <div
            className="h-full bg-primary-accent transition-[width] duration-(--motion-slow)"
            style={{ width: `${localPct}%` }}
            title={`本地 ${formatCost(data.local_cost)}`}
          />
          <div
            className="h-full bg-primary-accent/35 transition-[width] duration-(--motion-slow)"
            style={{ width: `${cloudPct}%` }}
            title={`云端 ${formatCost(data.cloud_cost)}`}
          />
        </div>
        <div className="mt-1.5 flex items-center justify-between text-xs">
          <span className="flex min-w-0 items-center gap-1.5">
            <span className="size-1.5 shrink-0 rounded-full bg-primary-accent" />
            <span className="text-muted-foreground">本地</span>
            <span className="truncate font-mono tabular-nums">{formatCost(data.local_cost)}</span>
          </span>
          <span className="flex min-w-0 items-center gap-1.5">
            <span className="size-1.5 shrink-0 rounded-full bg-primary-accent/35" />
            <span className="text-muted-foreground">云端</span>
            <span className="truncate font-mono tabular-nums">{formatCost(data.cloud_cost)}</span>
          </span>
        </div>
      </div>
    </Card>
  );
}
