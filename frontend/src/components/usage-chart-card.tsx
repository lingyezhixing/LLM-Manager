import { useState } from "react";

import { useQuery } from "@tanstack/react-query";

import { LineChart } from "lucide-react";

import { Card, Empty, Skeleton } from "@/components/ui/card";
import { ErrorState } from "@/components/ui/error-state";
import { SourcePills } from "@/components/source-pills";
import { TokenChart } from "@/components/token-chart";
import { fetchUsageCostSeries, fetchUsageSeries, type UsageSource, type UsageSeries, type UsageSeriesParams } from "@/lib/api";
import { errMsg, formatCost } from "@/lib/format";
import { chartPresetFor, EMPTY_COST, EMPTY_REQUESTS, type DateRange, type UsagePreset } from "@/lib/usage-range";
import { qk } from "@/lib/api/keys";

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
  const [source, setSource] = useState<UsageSource>("all");
  // 三视图共用 source 过滤(服务端参数;「按模型」视图每条线本就带归属)
  const filteredParams: UsageSeriesParams = { ...params, source };
  const { data, isLoading, isError, error, refetch: refetchSeries } = useQuery({
    queryKey: qk.usageSeries(filteredParams),
    queryFn: () => fetchUsageSeries(filteredParams),
    refetchInterval: refetch,
    enabled: view !== "cost",   // 成本视图不轮询 token 序列(与 cost-series 对称)
  });
  const costSeriesQ = useQuery({
    queryKey: qk.usageCostSeries(filteredParams),
    queryFn: () => fetchUsageCostSeries(filteredParams),
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
        <div className="flex items-center gap-2">
          <div className="flex items-center gap-1">
            {(["total", "models", "cost"] as const).map((v) => (
              <button
                key={v}
                type="button"
                aria-pressed={view === v}
                onClick={() => setView(v)}
                className={`rounded-full px-2.5 py-0.5 text-ui transition-colors duration-(--motion-base) ${
                  view === v ? "bg-primary-accent/12 font-medium text-primary-accent" : "text-muted-foreground hover:text-foreground"
                }`}
              >
                {v === "total" ? "总量" : v === "models" ? "按模型" : "成本"}
              </button>
            ))}
          </div>
          {view !== "models" && <span className="h-4 w-px bg-border" />}
          <SourcePills value={source} onChange={setSource} />
        </div>
      </div>
      {view === "cost" ? (
        costSeriesQ.isError ? (
          <div className="flex h-[192px] items-center justify-center">
            <ErrorState message={errMsg(costSeriesQ.error)} onRetry={() => costSeriesQ.refetch()} />
          </div>
        ) : costSeriesQ.isLoading || !costSeriesQ.data ? (
          <div className="flex h-[192px] items-center justify-center">
            <Skeleton rows={5} />
          </div>
        ) : costSeriesQ.data.buckets.length === 0 ? (
          <Empty label={EMPTY_COST} className="h-[192px]" />
        ) : (
          <TokenChart data={costSeriesQ.data} preset={chartPreset} formatY={formatCost} />
        )
      ) : isError ? (
        <div className="flex h-[192px] items-center justify-center">
          <ErrorState message={errMsg(error)} onRetry={() => refetchSeries()} />
        </div>
      ) : isLoading || !data ? (
        <div className="flex h-[192px] items-center justify-center">
          <Skeleton rows={5} />
        </div>
      ) : data.buckets.length === 0 ? (
        <Empty label={EMPTY_REQUESTS} className="h-[192px]" />
      ) : (
        <TokenChart data={view === "total" ? totalOnly(data) : data} preset={chartPreset} />
      )}
    </Card>
  );
}

function totalOnly(data: UsageSeries): UsageSeries {
  return { buckets: data.buckets, total: data.total, models: {} };
}
