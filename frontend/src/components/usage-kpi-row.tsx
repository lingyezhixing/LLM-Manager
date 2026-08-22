import { useQuery } from "@tanstack/react-query";

import { Card, Skeleton } from "@/components/ui/card";
import { ErrorState } from "@/components/ui/error-state";
import { InfoTile } from "@/components/ui/info-tile";
import { fetchUsageCost, fetchUsageSummary, type UsageSeriesParams } from "@/lib/api";
import { errMsg, formatCost, formatCount, formatPercent, formatTokens } from "@/lib/format";
import { qk } from "@/lib/api/keys";

export function UsageKpiRow({
  params,
  refetch,
}: {
  params: UsageSeriesParams;
  refetch: number | false;
}) {
  const { data, isLoading, isError, error, refetch: refetchSummary } = useQuery({
    queryKey: qk.usageSummary(params),
    queryFn: () => fetchUsageSummary(params),
    refetchInterval: refetch,
  });
  const costQ = useQuery({
    queryKey: qk.usageCost(params),
    queryFn: () => fetchUsageCost(params),
    refetchInterval: refetch,
  });
  if (isError) {
    return <Card><ErrorState message={errMsg(error)} onRetry={() => refetchSummary()} /></Card>;
  }
  if (isLoading || !data) {
    return <Card><Skeleton rows={3} /></Card>;
  }
  return (
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-7">
      <InfoTile label="输入" value={formatTokens(data.input_tokens)} valueClass="text-primary-accent" />
      <InfoTile label="输出" value={formatTokens(data.output_tokens)} />
      <InfoTile label="缓存命中" value={formatTokens(data.cache_hit)} valueClass="text-success" />
      <InfoTile label="未命中" value={formatTokens(data.cache_miss)} valueClass="text-destructive" />
      <InfoTile label="命中率" value={formatPercent(data.hit_rate, 1)} valueClass="text-primary-accent" />
      <InfoTile label="请求数" value={formatCount(data.request_count)} />
      <InfoTile
        label="成本"
        valueClass="text-primary-accent"
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
