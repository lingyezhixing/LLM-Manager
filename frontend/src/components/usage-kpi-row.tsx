import { useQuery } from "@tanstack/react-query";

import { ErrorState } from "@/components/ui/error-state";
import { InfoTile } from "@/components/ui/info-tile";
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
      <InfoTile label="输入" value={formatTokens(data.input_tokens)} valueClass="text-primary" />
      <InfoTile label="输出" value={formatTokens(data.output_tokens)} />
      <InfoTile label="缓存命中" value={formatTokens(data.cache_hit)} valueClass="text-success" />
      <InfoTile label="未命中" value={formatTokens(data.cache_miss)} valueClass="text-destructive" />
      <InfoTile label="命中率" value={formatHitRate(data.hit_rate)} valueClass="text-primary" />
      <InfoTile label="请求数" value={formatCount(data.request_count)} />
      <InfoTile
        label="成本"
        valueClass="text-primary"
        value={
          costQ.isError ? (
            <ErrorState onRetry={() => costQ.refetch()} />
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
