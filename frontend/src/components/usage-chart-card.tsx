import { useState } from "react";

import { useQuery } from "@tanstack/react-query";

import { LineChart } from "lucide-react";

import { Card, Empty, Loading } from "@/components/ui/card";
import { ErrorState } from "@/components/ui/error-state";
import { TokenChart } from "@/components/token-chart";
import { fetchUsageCostSeries, fetchUsageSeries, type UsageSeries, type UsageSeriesParams } from "@/lib/api";
import { errMsg, formatCost } from "@/lib/format";
import { chartPresetFor, type DateRange, type UsagePreset } from "@/lib/usage-range";

type View = "total" | "models" | "cost";

export function UsageChartCard({
  params,
  preset,
  custom,
  refetch,
}: {
  params: UsageSeriesParams;
  preset: UsagePreset;
  custom: DateRange | null;
  refetch: number | false;
}) {
  const [view, setView] = useState<View>("models");
  const { data, isLoading, isError, error, refetch: refetchSeries } = useQuery({
    queryKey: ["usage", "series", params],
    queryFn: () => fetchUsageSeries(params),
    refetchInterval: refetch,
  });
  const costSeriesQ = useQuery({
    queryKey: ["usage", "cost-series", params],
    queryFn: () => fetchUsageCostSeries(params),
    refetchInterval: refetch,
    enabled: view === "cost",
  });
  const chartPreset = chartPresetFor(preset, custom);
  return (
    <Card>
      <div className="mb-3 flex items-center justify-between">
        <span className="flex items-center gap-2 text-sm font-semibold">
          <LineChart className="size-4 text-primary-accent" />
          {view === "cost" ? "成本曲线" : "Token 消耗曲线"}
        </span>
        <div className="flex items-center gap-1">
          {(["total", "models", "cost"] as const).map((v) => (
            <button
              key={v}
              type="button"
              onClick={() => setView(v)}
              className={`rounded-full px-2.5 py-0.5 text-[11px] transition-colors ${
                view === v ? "bg-primary-accent/12 font-medium text-primary-accent" : "text-muted-foreground hover:text-foreground"
              }`}
            >
              {v === "total" ? "总量" : v === "models" ? "按模型" : "成本"}
            </button>
          ))}
        </div>
      </div>
      {view === "cost" ? (
        costSeriesQ.isError ? (
          <div className="flex h-[192px] items-center justify-center">
            <ErrorState message={errMsg(costSeriesQ.error)} onRetry={() => costSeriesQ.refetch()} />
          </div>
        ) : costSeriesQ.isLoading || !costSeriesQ.data ? (
          <div className="flex h-[192px] items-center justify-center">
            <Loading />
          </div>
        ) : costSeriesQ.data.buckets.length === 0 ? (
          <Empty label="该时间范围内暂无成本" className="h-[192px]" />
        ) : (
          <TokenChart data={costSeriesQ.data} preset={chartPreset} formatY={formatCost} />
        )
      ) : isError ? (
        <div className="flex h-[192px] items-center justify-center">
          <ErrorState message={errMsg(error)} onRetry={() => refetchSeries()} />
        </div>
      ) : isLoading || !data ? (
        <div className="flex h-[192px] items-center justify-center">
          <Loading />
        </div>
      ) : data.buckets.length === 0 ? (
        <Empty label="该时间范围内暂无请求" className="h-[192px]" />
      ) : (
        <TokenChart data={view === "total" ? totalOnly(data) : data} preset={chartPreset} />
      )}
    </Card>
  );
}

function totalOnly(data: UsageSeries): UsageSeries {
  return { buckets: data.buckets, total: data.total, models: {} };
}
