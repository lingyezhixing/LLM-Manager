import { useState } from "react";
import { Calendar } from "lucide-react";

import { CalendarRangePicker } from "@/components/calendar-range-picker";
import { fmtRange, rangeForState, USAGE_PRESETS, type UsageRangeState } from "@/lib/usage-range";

/** 预设胶囊 + 自定义日历范围选择器。驱动整个用量页。 */
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
          aria-pressed={value.preset === p.key}
          onClick={() => {
            onChange({ preset: p.key, custom: value.custom });
            setCalOpen(false);
          }}
          className={`rounded-full border border-border px-2.5 py-0.5 text-ui transition-colors duration-(--motion-base) ${
            value.preset === p.key ? "bg-primary-accent/12 font-medium text-primary-accent" : "text-muted-foreground hover:text-foreground"
          }`}
        >
          {p.label}
        </button>
      ))}
      <button
        type="button"
        onClick={() => setCalOpen(true)}
        aria-expanded={calOpen}
        aria-haspopup="dialog"
        className={`inline-flex items-center gap-1 rounded-full border border-border px-2.5 py-0.5 text-ui transition-colors duration-(--motion-base) ${
          value.preset === "custom" ? "bg-primary-accent/12 font-medium text-primary-accent" : "text-muted-foreground hover:text-foreground"
        }`}
      >
        <Calendar className="size-3" aria-hidden />
        {fmtRange(displayed)}
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
