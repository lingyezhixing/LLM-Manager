// Usage-page range state, presets, refetch cadence, params derivation, and the shared
// date-range math. Single source for BOTH range pickers (usage page + overview token
// card) — keeps preset semantics aligned with the backend _resolve_range (current-moment,
// NOT day-boundary). Separated from component files so they only export components
// (React Fast Refresh — see oxlint react/only-export-components).
import type { UsageSeriesParams } from "@/lib/api";

export interface DateRange {
  from: Date;
  to: Date;
}

export type UsagePreset = "10m" | "today" | "7d" | "30d" | "custom";

export interface UsageRangeState {
  preset: UsagePreset;
  custom: DateRange | null;
}

export const USAGE_PRESETS: { key: Exclude<UsagePreset, "custom">; label: string }[] = [
  { key: "10m", label: "十分钟内" },
  { key: "today", label: "今日" },
  { key: "7d", label: "7天" },
  { key: "30d", label: "30天" },
];

/** Refetch cadence for auto-refresh modules (KPI/chart/by-model). Custom = manual. */
export const USAGE_REFETCH: Record<UsagePreset, number | false> = {
  "10m": 10_000,
  today: 30_000,
  "7d": 60_000,
  "30d": 60_000,
  custom: false,
};

/** Preset → 显示区间(当前时刻语义,与后端 _resolve_range 一致:10m=now-600 等)。 */
export function rangeForPreset(preset: Exclude<UsagePreset, "custom">): DateRange {
  const to = new Date();
  const from = new Date();
  if (preset === "10m") from.setMinutes(from.getMinutes() - 10);
  else if (preset === "today") from.setHours(0, 0, 0, 0);
  else if (preset === "7d") from.setDate(from.getDate() - 7);
  else from.setDate(from.getDate() - 30);   // 30d
  return { from, to };
}

export function rangeForState(state: UsageRangeState): DateRange {
  if (state.preset === "custom" && state.custom) return state.custom;
  return rangeForPreset(state.preset as Exclude<UsagePreset, "custom">);
}

/** 区间显示文本(M-D ~ M-D)— 两个 picker 与 token 卡共用。 */
export function fmtRange(r: DateRange): string {
  const f = `${r.from.getMonth() + 1}-${r.from.getDate()}`;
  const t = `${r.to.getMonth() + 1}-${r.to.getDate()}`;
  return `${f} ~ ${t}`;
}

/** 图表 preset(自定义区间按 30d 粒度显示)— token 卡与用量页共用。 */
export function chartPresetFor(preset: UsagePreset): string {
  return preset === "custom" ? "30d" : preset;
}

export function paramsForState(state: UsageRangeState): UsageSeriesParams {
  if (state.preset === "custom" && state.custom) {
    return {
      start: Math.floor(state.custom.from.getTime() / 1000),
      end: Math.floor(state.custom.to.getTime() / 1000),
    };
  }
  return { period: state.preset };
}
