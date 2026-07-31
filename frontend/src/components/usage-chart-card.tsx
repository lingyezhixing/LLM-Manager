import { useState } from "react";

import { useQuery } from "@tanstack/react-query";

import { Button } from "@/components/ui/button";
import { TokenChart } from "@/components/token-chart";
import { fetchUsageCostSeries, fetchUsageSeries, type UsageSeries, type UsageSeriesParams } from "@/lib/api";
import { formatCost } from "@/lib/format";

type View = "total" | "models" | "cost";

export function UsageChartCard({
  params,
  preset,
  refetch,
}: {
  params: UsageSeriesParams;
  preset: string;
  refetch: number | false;
}) {
  const [view, setView] = useState<View>("models");
  const { data, isLoading } = useQuery({
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
  const chartPreset = preset === "custom" ? "30d" : preset;
  return (
    <div className="rounded-lg border border-border p-4">
      <div className="mb-3 flex items-center justify-between">
        <span className="text-xs uppercase tracking-wide text-muted-foreground">
          {view === "cost" ? "成本曲线(元)" : "Token 消耗曲线"}
        </span>
        <div className="flex overflow-hidden rounded-md border border-border">
          <button
            type="button"
            onClick={() => setView("total")}
            className={`px-2.5 py-0.5 text-[11px] ${view === "total" ? "bg-muted font-medium text-foreground" : "text-muted-foreground"}`}
          >
            总量
          </button>
          <button
            type="button"
            onClick={() => setView("models")}
            className={`px-2.5 py-0.5 text-[11px] ${view === "models" ? "bg-muted font-medium text-foreground" : "text-muted-foreground"}`}
          >
            按模型
          </button>
          <button
            type="button"
            onClick={() => setView("cost")}
            className={`px-2.5 py-0.5 text-[11px] ${view === "cost" ? "bg-muted font-medium text-foreground" : "text-muted-foreground"}`}
          >
            成本
          </button>
        </div>
      </div>
      {view === "cost" ? (
        costSeriesQ.isError ? (
          <div className="flex h-[240px] items-center justify-center gap-2 text-sm text-destructive">
            加载失败:{(costSeriesQ.error as Error).message}
            <Button size="sm" variant="ghost" onClick={() => costSeriesQ.refetch()}>重试</Button>
          </div>
        ) : costSeriesQ.isLoading || !costSeriesQ.data ? (
          <div className="flex h-[240px] items-center justify-center text-sm text-muted-foreground">加载中…</div>
        ) : costSeriesQ.data.buckets.length === 0 ? (
          <div className="flex h-[240px] items-center justify-center rounded-lg border border-dashed border-border text-sm text-muted-foreground">该时间范围内暂无成本</div>
        ) : (
          <TokenChart data={costSeriesQ.data} preset={chartPreset} formatY={formatCost} />
        )
      ) : isLoading || !data ? (
        <div className="flex h-[240px] items-center justify-center text-sm text-muted-foreground">加载中…</div>
      ) : data.buckets.length === 0 ? (
        <div className="flex h-[240px] items-center justify-center rounded-lg border border-dashed border-border text-sm text-muted-foreground">
          该时间范围内暂无请求
        </div>
      ) : (
        <TokenChart data={view === "total" ? totalOnly(data) : data} preset={chartPreset} />
      )}
    </div>
  );
}

function totalOnly(data: UsageSeries): UsageSeries {
  return { buckets: data.buckets, total: data.total, models: {} };
}
