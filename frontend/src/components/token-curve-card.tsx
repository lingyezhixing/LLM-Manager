import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { LineChart } from "lucide-react";

import { ErrorState } from "@/components/ui/error-state";
import { TokenChart } from "@/components/token-chart";
import { UsageRangePicker } from "@/components/usage-range-picker";
import { fetchUsageSeries, type UsageSeriesParams } from "@/lib/api";
import { errMsg } from "@/lib/format";
import { chartPresetFor, paramsForState, USAGE_REFETCH, type UsageRangeState } from "@/lib/usage-range";

/** Token 消耗 card:preset 胶囊 + 自选日历(复用用量页的 UsageRangePicker)。区间/节奏/
 * 参数推导与用量页共用 lib/usage-range(语义与后端 _resolve_range 一致:当前时刻,非日界)。 */
export function TokenCurveCard() {
  const [range, setRange] = useState<UsageRangeState>({ preset: "7d", custom: null });
  const params: UsageSeriesParams = paramsForState(range);

  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ["usage", "series", params],
    queryFn: () => fetchUsageSeries(params),
    refetchInterval: USAGE_REFETCH[range.preset],
  });

  return (
    <div className="rounded-lg border border-border bg-card p-4 shadow-card">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <span className="flex items-center gap-2 text-sm font-semibold">
          <LineChart className="size-4 text-primary-accent" />
          Token 消耗
        </span>
        <UsageRangePicker value={range} onChange={setRange} />
      </div>
      {isError ? (
        <div className="flex h-[160px] items-center justify-center">
          <ErrorState message={errMsg(error)} onRetry={() => refetch()} />
        </div>
      ) : isLoading || !data ? (
        <div className="flex h-[160px] items-center justify-center text-sm text-muted-foreground">加载中…</div>
      ) : (
        <TokenChart data={data} preset={chartPresetFor(range.preset, range.custom)} />
      )}
    </div>
  );
}
