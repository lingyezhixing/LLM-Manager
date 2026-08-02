import { type ReactNode } from "react";

import { useQuery } from "@tanstack/react-query";

import { Button } from "@/components/ui/button";
import { fetchUsageCost, fetchUsageSummary, type UsageSeriesParams } from "@/lib/api";
import { formatCost, formatCount, formatHitRate, formatTokens } from "@/lib/format";

export function UsageKpiRow({
  params,
  refetch,
}: {
  params: UsageSeriesParams;
  refetch: number | false;
}) {
  const { data, isLoading } = useQuery({
    queryKey: ["usage", "summary", params],
    queryFn: () => fetchUsageSummary(params),
    refetchInterval: refetch,
  });
  const costQ = useQuery({
    queryKey: ["usage", "cost", params],
    queryFn: () => fetchUsageCost(params),
    refetchInterval: refetch,
  });
  if (isLoading || !data) {
    return (
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-7">
        {Array.from({ length: 7 }).map((_, i) => (
          <div key={i} className="h-[58px] rounded-lg border border-border" />
        ))}
      </div>
    );
  }
  return (
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-7">
      <Tile label="输入" value={formatTokens(data.input_tokens)} valueClass="text-primary" />
      <Tile label="输出" value={formatTokens(data.output_tokens)} />
      <Tile label="缓存命中" value={formatTokens(data.cache_hit)} valueClass="text-success" />
      <Tile label="未命中" value={formatTokens(data.cache_miss)} valueClass="text-destructive" />
      <Tile label="命中率" value={formatHitRate(data.hit_rate)} valueClass="text-primary" />
      <Tile label="请求数" value={formatCount(data.request_count)} />
      <Tile
        label="成本"
        valueClass="text-primary"
        value={
          costQ.isError ? (
            <span className="flex items-center gap-1.5 text-destructive">
              加载失败
              <Button size="sm" variant="ghost" onClick={() => costQ.refetch()}>重试</Button>
            </span>
          ) : costQ.data ? (
            formatCost(costQ.data.total_cost)
          ) : (
            "—"
          )
        }
      />
    </div>
  );
}

function Tile({ label, value, valueClass = "" }: { label: string; value: ReactNode; valueClass?: string }) {
  return (
    <div className="rounded-lg border border-border px-3 py-2">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className={`mt-0.5 text-base font-semibold ${valueClass}`}>{value}</div>
    </div>
  );
}
