import { useState } from "react";

import { useQuery } from "@tanstack/react-query";

import { TokenChart } from "@/components/token-chart";
import { fetchUsageSeries, type UsageSeries, type UsageSeriesParams } from "@/lib/api";

type View = "total" | "models";

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
  const chartPreset = preset === "custom" ? "30d" : preset;
  return (
    <div className="rounded-lg border border-border p-4">
      <div className="mb-3 flex items-center justify-between">
        <span className="text-xs uppercase tracking-wide text-muted-foreground">Token 消耗曲线</span>
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
        </div>
      </div>
      {isLoading || !data ? (
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
