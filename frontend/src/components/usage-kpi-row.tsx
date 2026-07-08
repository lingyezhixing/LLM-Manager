import { useQuery } from "@tanstack/react-query";

import { fetchUsageSummary, type UsageSeriesParams } from "@/lib/api";
import { formatCount, formatHitRate, formatTokens } from "@/lib/format";

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
  if (isLoading || !data) {
    return (
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
        {Array.from({ length: 6 }).map((_, i) => (
          <div key={i} className="h-[58px] rounded-lg border border-border" />
        ))}
      </div>
    );
  }
  return (
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
      <Tile label="输入" value={formatTokens(data.input_tokens)} valueClass="text-primary" />
      <Tile label="输出" value={formatTokens(data.output_tokens)} />
      <Tile label="缓存命中" value={formatTokens(data.cache_hit)} valueClass="text-success" />
      <Tile label="未命中" value={formatTokens(data.cache_miss)} valueClass="text-destructive" />
      <Tile label="命中率" value={formatHitRate(data.hit_rate)} valueClass="text-primary" />
      <Tile label="请求数" value={formatCount(data.request_count)} />
    </div>
  );
}

function Tile({ label, value, valueClass = "" }: { label: string; value: string; valueClass?: string }) {
  return (
    <div className="rounded-lg border border-border px-3 py-2">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className={`mt-0.5 text-base font-semibold ${valueClass}`}>{value}</div>
    </div>
  );
}
