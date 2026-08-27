import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { LineChart } from "lucide-react";

import { Card, Empty, Skeleton } from "@/components/ui/card";
import { ErrorState } from "@/components/ui/error-state";
import { TokenChart } from "@/components/token-chart";
import { UsageRangePicker } from "@/components/usage-range-picker";
import { fetchUsageSeries, type UsageSource, type UsageSeriesParams } from "@/lib/api";
import { errMsg } from "@/lib/format";
import { chartPresetFor, EMPTY_REQUESTS, paramsForState, USAGE_REFETCH, type UsageRangeState } from "@/lib/usage-range";
import { qk } from "@/lib/api/keys";
import { SourcePills } from "@/components/source-pills";

/** Token 消耗 card:preset 胶囊 + 自选日历(复用用量页的 UsageRangePicker)。区间/节奏/
 * 参数推导与用量页共用 lib/usage-range(语义与后端 _resolve_range 一致:当前时刻,非日界)。 */
export function TokenCurveCard() {
  const [range, setRange] = useState<UsageRangeState>({ preset: "7d", custom: null });
  const [source, setSource] = useState<UsageSource>("all");
  const params: UsageSeriesParams = { ...paramsForState(range), source };

  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: qk.usageSeries(params),
    queryFn: () => fetchUsageSeries(params),
    refetchInterval: USAGE_REFETCH[range.preset],
  });

  return (
    <Card>
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <span className="flex items-center gap-2 text-sm font-semibold">
          <LineChart className="size-4 text-primary-accent" />
          Token 消耗
        </span>
        <div className="flex items-center gap-2">
          <SourcePills value={source} onChange={setSource} />
          <UsageRangePicker value={range} onChange={setRange} />
        </div>
      </div>
      {isError ? (
        <div className="flex h-[160px] items-center justify-center">
          <ErrorState message={errMsg(error)} onRetry={() => refetch()} />
        </div>
      ) : isLoading || !data ? (
        <div className="flex h-[160px] items-center justify-center">
          <Skeleton rows={5} />
        </div>
      ) : data.buckets.length === 0 ? (
        <Empty label={EMPTY_REQUESTS} className="h-[160px]" />
      ) : (
        <TokenChart data={data} preset={chartPresetFor(range.preset, range.custom)} />
      )}
    </Card>
  );
}
