import { useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { CalendarRangePicker, type DateRange } from "@/components/calendar-range-picker";
import { TokenChart } from "@/components/token-chart";
import { fetchUsageSeries, type UsageSeriesParams } from "@/lib/api";

type Preset = "10m" | "today" | "7d" | "30d" | "custom";

const PRESETS: { key: Exclude<Preset, "custom">; label: string }[] = [
  { key: "10m", label: "十分钟内" },
  { key: "today", label: "今日" },
  { key: "7d", label: "7天" },
  { key: "30d", label: "30天" },
];

/** Refetch cadence per preset (ms); custom = no auto-refresh. */
const REFETCH: Record<Preset, number | false> = {
  "10m": 3000,
  today: 60_000,
  "7d": 600_000,
  "30d": 3_600_000,
  custom: false,
};

function fmtDate(d: Date): string {
  const p = (x: number) => String(x).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

function fmtRange(r: DateRange): string {
  return `${fmtDate(r.from)} ~ ${fmtDate(r.to)}`;
}

function startOfToday(): Date {
  const d = new Date();
  d.setHours(0, 0, 0, 0);
  return d;
}

/** Date range a preset corresponds to (day granularity) — drives the 自选 pill display. */
function rangeForPreset(preset: Preset): DateRange {
  const today = startOfToday();
  if (preset === "7d") return { from: new Date(today.getTime() - 7 * 86_400_000), to: today };
  if (preset === "30d") return { from: new Date(today.getTime() - 30 * 86_400_000), to: today };
  return { from: today, to: today }; // 10m, today
}

/** Token 消耗 card: preset bar (+ 自选 calendar) in the header, hand-rolled chart below. */
export function TokenCurveCard() {
  const [preset, setPreset] = useState<Preset>("7d");
  const [custom, setCustom] = useState<DateRange>(() => rangeForPreset("7d"));
  const [calOpen, setCalOpen] = useState(false);
  const displayedRange = preset === "custom" ? custom : rangeForPreset(preset);

  const params: UsageSeriesParams =
    preset === "custom" && custom
      ? { start: Math.floor(custom.from.getTime() / 1000), end: Math.floor(custom.to.getTime() / 1000) }
      : { range: preset };

  const { data, isLoading } = useQuery({
    queryKey: ["usage", "series", params],
    queryFn: () => fetchUsageSeries(params),
    refetchInterval: REFETCH[preset],
  });

  return (
    <div className="rounded-lg border border-border p-3">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <span className="text-sm font-semibold">Token 消耗</span>
        <div className="relative flex flex-wrap items-center gap-1">
          {PRESETS.map((p) => (
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
            {fmtRange(displayedRange)}
          </button>
          {calOpen && (
            <CalendarRangePicker
              value={displayedRange}
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
      {isLoading || !data ? (
        <div className="flex h-[200px] items-center justify-center text-sm text-muted-foreground">加载中…</div>
      ) : (
        <TokenChart data={data} preset={preset === "custom" ? "30d" : preset} />
      )}
    </div>
  );
}
