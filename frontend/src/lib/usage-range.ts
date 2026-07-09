// Usage-page range state, presets, refetch cadence, and params derivation.
// Separated from the picker component so the component file only exports a component
// (keeps React Fast Refresh happy — see oxlint react/only-export-components).
import type { DateRange } from "@/components/calendar-range-picker";

import type { UsageSeriesParams } from "@/lib/api";

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

export function paramsForState(state: UsageRangeState): UsageSeriesParams {
  if (state.preset === "custom" && state.custom) {
    return {
      start: Math.floor(state.custom.from.getTime() / 1000),
      end: Math.floor(state.custom.to.getTime() / 1000),
    };
  }
  return { range: state.preset };
}
