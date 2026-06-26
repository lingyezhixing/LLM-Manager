import { useRef, useState, type MouseEvent } from "react";

import type { UsageSeries } from "@/lib/api";

/** Hand-rolled multi-series line chart (no chart lib — offline, minimal, theme-aware).
 *  total = primary line + area; per-model = categorical colors. Legend toggles models;
 *  hover shows a guide line + a cursor-following floating tooltip (no layout shift). */

const MODEL_COLORS = ["#f97316", "#a855f7", "#22c55e", "#eab308", "#ec4899", "#06b6d4"];

const W = 760;
const H = 260;
const PAD = { l: 44, r: 16, t: 16, b: 28 };
const PLOT_W = W - PAD.l - PAD.r;   // 700
const PLOT_H = H - PAD.t - PAD.b;    // 216

function fmtTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${Math.round(n / 1_000)}K`;
  return `${n}`;
}

function fmtTs(ts: number, preset: string): string {
  const d = new Date(ts * 1000);
  const p = (x: number) => String(x).padStart(2, "0");
  const md = `${p(d.getMonth() + 1)}-${p(d.getDate())}`;
  const hm = `${p(d.getHours())}:${p(d.getMinutes())}`;
  if (preset === "10m") return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
  if (preset === "today") return hm;
  if (preset === "7d") return `${md} ${hm}`;
  return md; // 30d / custom
}

function LegendDot({ color, label }: { color: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1">
      <span className="inline-block size-2 rounded-full" style={{ background: color }} />
      {label}
    </span>
  );
}

export function TokenChart({ data, preset }: { data: UsageSeries; preset: string }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [hover, setHover] = useState<number | null>(null);
  const [pos, setPos] = useState<{ x: number; y: number } | null>(null);

  const { buckets, total, models } = data;
  const modelNames = Object.keys(models);
  const n = buckets.length;

  if (n === 0) {
    return (
      <div className="flex h-[200px] items-center justify-center text-sm text-muted-foreground">
        暂无数据
      </div>
    );
  }

  const visibleNames = modelNames.filter((m) => !hidden.has(m));
  const allValues = [...total, ...visibleNames.flatMap((m) => models[m])];
  const max = Math.max(1, ...allValues);

  const xAt = (i: number) => (n === 1 ? PAD.l + PLOT_W / 2 : PAD.l + (i / (n - 1)) * PLOT_W);
  const yAt = (v: number) => PAD.t + PLOT_H - (v / max) * PLOT_H;

  const line = (series: number[]) =>
    series.map((v, i) => `${i === 0 ? "M" : "L"}${xAt(i).toFixed(1)},${yAt(v).toFixed(1)}`).join(" ");
  const area = (series: number[]) =>
    `${line(series)} L${xAt(n - 1).toFixed(1)},${(PAD.t + PLOT_H).toFixed(1)} L${xAt(0).toFixed(1)},${(PAD.t + PLOT_H).toFixed(1)} Z`;

  const yTicks = [1, 0.75, 0.5, 0.25, 0].map((f) => ({ v: max * f, y: yAt(max * f) }));
  const labelCount = Math.min(5, n);
  const xLabelIdx = Array.from({ length: labelCount }, (_, k) =>
    labelCount === 1 ? 0 : Math.round((k * (n - 1)) / (labelCount - 1)),
  );

  const toggle = (m: string) =>
    setHidden((prev) => {
      const next = new Set(prev);
      if (next.has(m)) next.delete(m);
      else next.add(m);
      return next;
    });

  const onMove = (e: MouseEvent<SVGSVGElement>) => {
    const svgRect = e.currentTarget.getBoundingClientRect();
    const xRel = ((e.clientX - svgRect.left) / svgRect.width) * W;
    const i = n === 1 ? 0 : Math.round(((xRel - PAD.l) / PLOT_W) * (n - 1));
    setHover(Math.max(0, Math.min(n - 1, i)));
    const container = containerRef.current;
    if (container) {
      const cr = container.getBoundingClientRect();
      setPos({ x: e.clientX - cr.left, y: e.clientY - cr.top });
    }
  };
  const onLeave = () => {
    setHover(null);
    setPos(null);
  };

  return (
    <div ref={containerRef} className="relative text-muted-foreground">
      {/* legend */}
      <div className="mb-2 flex flex-wrap gap-x-4 gap-y-1 text-xs">
        <LegendDot color="var(--color-primary)" label="总量" />
        {modelNames.map((m, i) => (
          <button
            key={m}
            type="button"
            onClick={() => toggle(m)}
            className={hidden.has(m) ? "opacity-40" : ""}
          >
            <LegendDot color={MODEL_COLORS[i % MODEL_COLORS.length]} label={m} />
          </button>
        ))}
      </div>

      <svg viewBox={`0 0 ${W} ${H}`} width="100%" onMouseMove={onMove} onMouseLeave={onLeave}>
        {/* gridlines + y labels */}
        {yTicks.map((t, i) => (
          <g key={i}>
            <line x1={PAD.l} y1={t.y} x2={W - PAD.r} y2={t.y} stroke="currentColor" strokeOpacity={i === 4 ? 0.3 : 0.12} />
            <text x={PAD.l - 6} y={t.y + 3} textAnchor="end" fontSize="10" fill="currentColor">
              {fmtTokens(t.v)}
            </text>
          </g>
        ))}
        {/* x labels */}
        {xLabelIdx.map((i) => (
          <text key={i} x={xAt(i)} y={H - 8} textAnchor="middle" fontSize="10" fill="currentColor">
            {fmtTs(buckets[i], preset)}
          </text>
        ))}
        {/* total line + area (primary) */}
        <g className="text-primary">
          <path d={area(total)} fill="currentColor" fillOpacity={0.12} />
          <path d={line(total)} fill="none" stroke="currentColor" strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
        </g>
        {/* per-model lines */}
        {visibleNames.map((m) => {
          const ci = modelNames.indexOf(m);
          return (
            <path
              key={m}
              d={line(models[m])}
              fill="none"
              stroke={MODEL_COLORS[ci % MODEL_COLORS.length]}
              strokeWidth={1.6}
              strokeLinejoin="round"
              strokeLinecap="round"
            />
          );
        })}
        {/* hover guide + total point */}
        {hover !== null && (
          <line
            x1={xAt(hover)}
            y1={PAD.t}
            x2={xAt(hover)}
            y2={PAD.t + PLOT_H}
            stroke="currentColor"
            strokeOpacity={0.35}
            strokeDasharray="3 3"
          />
        )}
      </svg>

      {/* cursor-following floating tooltip (absolute → no layout shift) */}
      {hover !== null && pos !== null && (
        <div
          className="pointer-events-none absolute z-10 min-w-[120px] rounded-md border border-border bg-card px-2 py-1 text-xs shadow-sm"
          style={{ left: pos.x + 14, top: pos.y + 14 }}
        >
          <div className="mb-0.5 text-foreground">{fmtTs(buckets[hover], preset)}</div>
          <div>
            总量 <span className="text-foreground">{fmtTokens(total[hover])}</span>
          </div>
          {visibleNames.map((m) => {
            const ci = modelNames.indexOf(m);
            return (
              <div key={m} style={{ color: MODEL_COLORS[ci % MODEL_COLORS.length] }}>
                {m} {fmtTokens(models[m][hover])}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
