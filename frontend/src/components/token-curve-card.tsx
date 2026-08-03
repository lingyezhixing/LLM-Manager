import { useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { CalendarRangePicker } from "@/components/calendar-range-picker";
import { ErrorState } from "@/components/ui/error-state";
import { TokenChart } from "@/components/token-chart";
import { fetchUsageSeries, type UsageSeriesParams } from "@/lib/api";
import {
  chartPresetFor,
  fmtRange,
  paramsForState,
  rangeForState,
  USAGE_PRESETS,
  USAGE_REFETCH,
  type DateRange,
  type UsagePreset,
  type UsageRangeState,
} from "@/lib/usage-range";

/** Token 消耗 card:preset 胶囊 + 自选日历。区间/节奏/参数推导与用量页共用 lib/usage-range
 * (语义与后端 _resolve_range 一致:当前时刻,非日界)。 */
export function TokenCurveCard() {
  const [preset, setPreset] = useState<UsagePreset>("7d");
  const [custom, setCustom] = useState<DateRange | null>(null);
  const [calOpen, setCalOpen] = useState(false);
  const range: UsageRangeState = { preset, custom };

  const displayed = rangeForState(range);
  const params: UsageSeriesParams = paramsForState(range);

  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ["usage", "series", params],
    queryFn: () => fetchUsageSeries(params),
    refetchInterval: USAGE_REFETCH[preset],
  });

  return (
    <div className="rounded-lg border border-border p-3">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <span className="text-sm font-semibold">Token 消耗</span>
        <div className="relative flex flex-wrap items-center gap-1">
          {USAGE_PRESETS.map((p) => (
            <button
              key={p.key}
              type="button"
              onClick={() => {
                setPreset(p.key);
                setCalOpen(false);
              }}
              className={`rounded-full border border-border px-2.5 py-0.5 text-[11px] ${
                preset === p.key ? "bg-muted font-medium text-foreground" : "text-muted-foreground hover:text-foreground"
              }`}
            >
              {p.label}
            </button>
          ))}
          <button
            type="button"
            onClick={() => setCalOpen(true)}
            className={`rounded-full border border-border px-2.5 py-0.5 text-[11px] ${
              preset === "custom" ? "bg-muted font-medium text-foreground" : "text-muted-foreground hover:text-foreground"
            }`}
          >
            {fmtRange(displayed)}
          </button>
          {calOpen && (
            <CalendarRangePicker
              value={displayed}
              onChange={(r) => {
                setCustom(r);
                setPreset("custom");
                setCalOpen(false);
              }}
              onClose={() => setCalOpen(false)}
            />
          )}
        </div>
      </div>
      {isError ? (
        <div className="flex h-[200px] items-center justify-center">
          <ErrorState message={(error as Error).message} onRetry={() => refetch()} />
        </div>
      ) : isLoading || !data ? (
        <div className="flex h-[200px] items-center justify-center text-sm text-muted-foreground">加载中…</div>
      ) : (
        <TokenChart data={data} preset={chartPresetFor(preset)} />
      )}
    </div>
  );
}
