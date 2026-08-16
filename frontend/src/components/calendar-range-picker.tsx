import { useState } from "react";

import type { DateRange } from "@/lib/usage-range";

/** Hand-rolled two-month range picker (no lib — offline, matches the locked mockup).
 *  Each month navigates independently, so a range can span more than two months.
 *  Click a start day, then an end day; the range commits and the popover closes. */

const WEEKDAYS = ["一", "二", "三", "四", "五", "六", "日"];

function startOfMonth(d: Date): Date {
  return new Date(d.getFullYear(), d.getMonth(), 1);
}
function addMonths(d: Date, n: number): Date {
  return new Date(d.getFullYear(), d.getMonth() + n, 1);
}
function daysInMonth(y: number, m: number): number {
  return new Date(y, m + 1, 0).getDate();
}
function isSameDay(a: Date, b: Date): boolean {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}
function inRange(d: Date, a: Date, b: Date): boolean {
  const t = d.getTime();
  return t >= a.getTime() && t <= b.getTime();
}
function monthLabel(d: Date): string {
  return `${d.getFullYear()}年${d.getMonth() + 1}月`;
}

function MonthGrid({
  view,
  start,
  end,
  onPick,
}: {
  view: Date;
  start: Date | null;
  end: Date | null;
  onPick: (d: Date) => void;
}) {
  const y = view.getFullYear();
  const m = view.getMonth();
  const dim = daysInMonth(y, m);
  const lead = (new Date(y, m, 1).getDay() + 6) % 7; // Mon-start offset
  const cells: (Date | null)[] = [
    ...Array<null>(lead).fill(null),
    ...Array.from({ length: dim }, (_, i) => new Date(y, m, i + 1)),
  ];
  return (
    <div className="w-[168px]">
      <div className="mb-1 grid grid-cols-7 text-center text-micro text-muted-foreground">
        {WEEKDAYS.map((w) => (
          <div key={w} className="py-0.5">{w}</div>
        ))}
      </div>
      <div className="grid grid-cols-7 gap-0.5">
        {cells.map((d, i) => {
          if (d === null) return <div key={i} />;
          const isStart = start && isSameDay(d, start);
          const isEnd = end && isSameDay(d, end);
          const isMid = start && end && inRange(d, start, end) && !isStart && !isEnd;
          return (
            <button
              key={i}
              type="button"
              onClick={() => onPick(d)}
              className={[
                "h-6 rounded text-ui",
                isStart || isEnd
                  ? "bg-primary text-primary-foreground"
                  : isMid
                    ? "bg-primary/20"
                    : "text-foreground hover:bg-muted",
              ].join(" ")}
            >
              {d.getDate()}
            </button>
          );
        })}
      </div>
    </div>
  );
}

function MonthPanel({
  view,
  onShift,
  start,
  end,
  onPick,
}: {
  view: Date;
  onShift: (delta: number) => void;
  start: Date | null;
  end: Date | null;
  onPick: (d: Date) => void;
}) {
  return (
    <div>
      <div className="mb-1 flex items-center justify-between text-xs font-medium">
        <button type="button" className="px-1" onClick={() => onShift(-1)}>‹</button>
        <span>{monthLabel(view)}</span>
        <button type="button" className="px-1" onClick={() => onShift(1)}>›</button>
      </div>
      <MonthGrid view={view} start={start} end={end} onPick={onPick} />
    </div>
  );
}

export function CalendarRangePicker({
  value,
  onChange,
  onClose,
}: {
  value: DateRange | null;
  onChange: (r: DateRange) => void;
  onClose: () => void;
}) {
  const [leftView, setLeftView] = useState<Date>(() => startOfMonth(value?.from ?? new Date()));
  const [rightView, setRightView] = useState<Date>(() =>
    value?.to ? startOfMonth(value.to) : addMonths(startOfMonth(value?.from ?? new Date()), 1),
  );
  const [start, setStart] = useState<Date | null>(value?.from ?? null);
  const [end, setEnd] = useState<Date | null>(value?.to ?? null);

  const onPick = (d: Date) => {
    if (!start || (start && end)) {
      setStart(d);
      setEnd(null);
      return;
    }
    if (d.getTime() < start.getTime()) {
      setStart(d);
      setEnd(null);
      return;
    }
    // F4:to 取所选日 23:59:59.999。后端时间窗右开 [start,end),若用 00:00 会漏掉结束日全天。
    const endOfDay = new Date(d.getFullYear(), d.getMonth(), d.getDate(), 23, 59, 59, 999);
    setEnd(endOfDay);
    onChange({ from: start, to: endOfDay });
    onClose();
  };

  return (
    <>
      {/* click-outside backdrop */}
      <button
        type="button"
        aria-label="关闭"
        className="fixed inset-0 z-10 cursor-default"
        onClick={onClose}
      />
      <div className="absolute right-0 top-full z-20 mt-1 flex gap-5 rounded-lg border border-border bg-card p-3 shadow-card">
        <MonthPanel
          view={leftView}
          onShift={(d) => setLeftView((v) => addMonths(v, d))}
          start={start}
          end={end}
          onPick={onPick}
        />
        <MonthPanel
          view={rightView}
          onShift={(d) => setRightView((v) => addMonths(v, d))}
          start={start}
          end={end}
          onPick={onPick}
        />
      </div>
    </>
  );
}
