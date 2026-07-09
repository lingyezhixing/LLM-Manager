import { useState } from "react";

import { CalendarRangePicker, type DateRange } from "@/components/calendar-range-picker";
import { USAGE_PRESETS, type UsageRangeState } from "@/lib/usage-range";

function rangeForPreset(preset: "10m" | "today" | "7d" | "30d"): DateRange {
  const to = new Date();
  const from = new Date();
  if (preset === "10m") from.setMinutes(from.getMinutes() - 10);
  else if (preset === "today") from.setHours(0, 0, 0, 0);
  else if (preset === "7d") from.setDate(from.getDate() - 7);
  else from.setDate(from.getDate() - 30); // 30d
  return { from, to };
}

function rangeForState(state: UsageRangeState): DateRange {
  if (state.preset === "custom" && state.custom) return state.custom;
  return rangeForPreset(state.preset as "10m" | "today" | "7d" | "30d");
}

function fmtRange(r: DateRange): string {
  const f = `${r.from.getMonth() + 1}-${r.from.getDate()}`;
  const t = `${r.to.getMonth() + 1}-${r.to.getDate()}`;
  return `${f} ~ ${t}`;
}

/** Preset capsules + custom calendar range picker. Drives the whole usage page. */
export function UsageRangePicker({
  value,
  onChange,
}: {
  value: UsageRangeState;
  onChange: (s: UsageRangeState) => void;
}) {
  const [calOpen, setCalOpen] = useState(false);
  const displayed = rangeForState(value);
  return (
    <div className="relative flex flex-wrap items-center gap-1">
      {USAGE_PRESETS.map((p) => (
        <button
          key={p.key}
          type="button"
          onClick={() => {
            onChange({ preset: p.key, custom: value.custom });
            setCalOpen(false);
          }}
          className={`rounded-full border border-border px-2.5 py-0.5 text-[11px] ${
            value.preset === p.key ? "bg-muted font-medium text-foreground" : "text-muted-foreground hover:text-foreground"
          }`}
        >
          {p.label}
        </button>
      ))}
      <button
        type="button"
        onClick={() => setCalOpen(true)}
        className={`rounded-full border border-border px-2.5 py-0.5 text-[11px] ${
          value.preset === "custom" ? "bg-muted font-medium text-foreground" : "text-muted-foreground hover:text-foreground"
        }`}
      >
        📅 {fmtRange(displayed)}
      </button>
      {calOpen && (
        <CalendarRangePicker
          value={displayed}
          onChange={(r) => {
            onChange({ preset: "custom", custom: r });
            setCalOpen(false);
          }}
          onClose={() => setCalOpen(false)}
        />
      )}
    </div>
  );
}
