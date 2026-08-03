import { useState } from "react";

import { CalendarRangePicker } from "@/components/calendar-range-picker";
import { fmtRange, rangeForState, USAGE_PRESETS, type UsageRangeState } from "@/lib/usage-range";

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
