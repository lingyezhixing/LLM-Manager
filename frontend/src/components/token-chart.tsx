import { useRef, useState, type MouseEvent } from "react";

import type { UsageSeries } from "@/lib/api";
import { formatTokens } from "@/lib/format";

/** Hand-rolled token chart (no lib). Smooth (monotone-cubic) curves. Overlays a total
 *  area+line (primary) with thin per-model lines; legend toggles series. Theme-aware via
 *  currentColor; cursor-following tooltip. */

const MODEL_COLORS = ["#f97316", "#a855f7", "#22c55e", "#eab308", "#ec4899", "#06b6d4", "#3b82f6", "#ef4444"];

const W = 760;
const H = 192;   // 240 的 4/5:两页曲线图统一缩减高度(视觉平衡)
const PAD = { l: 44, r: 16, t: 16, b: 28 };
const PLOT_W = W - PAD.l - PAD.r;
const PLOT_H = H - PAD.t - PAD.b;

type Pt = [number, number];

function fmtTs(ts: number, preset: string): string {
  const d = new Date(ts * 1000);
  const p = (x: number) => String(x).padStart(2, "0");
  const md = `${p(d.getMonth() + 1)}-${p(d.getDate())}`;
  const hm = `${p(d.getHours())}:${p(d.getMinutes())}`;
  if (preset === "10m") return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
  if (preset === "today") return hm;
  if (preset === "7d") return `${md} ${hm}`;
  return md;
}

/** Monotone cubic (Fritsch-Carlson) → cubic bezier segments (no leading M).
 *  Monotone interpolation never overshoots the data, so a non-negative series stays
 *  non-negative and stacked-area bands never cross. */
function smoothSegments(pts: Pt[]): string {
  const n = pts.length;
  if (n < 2) return "";
  const dx: number[] = [];
  const tangent: number[] = [];
  for (let i = 0; i < n - 1; i++) {
    dx[i] = pts[i + 1][0] - pts[i][0];
    tangent[i] = dx[i] !== 0 ? (pts[i + 1][1] - pts[i][1]) / dx[i] : 0;
  }
  const m: number[] = new Array(n);
  m[0] = tangent[0];
  m[n - 1] = tangent[n - 2];
  for (let i = 1; i < n - 1; i++) {
    m[i] = tangent[i - 1] * tangent[i] <= 0 ? 0 : (tangent[i - 1] + tangent[i]) / 2;
  }
  for (let i = 0; i < n - 1; i++) {
    if (tangent[i] === 0) {
      m[i] = 0;
      m[i + 1] = 0;
      continue;
    }
    const a = m[i] / tangent[i];
    const b = m[i + 1] / tangent[i];
    const s = a * a + b * b;
    if (s > 9) {
      const tau = 3 / Math.sqrt(s);
      m[i] = tau * a * tangent[i];
      m[i + 1] = tau * b * tangent[i];
    }
  }
  let d = "";
  for (let i = 0; i < n - 1; i++) {
    const c1x = pts[i][0] + dx[i] / 3;
    const c1y = pts[i][1] + m[i] * dx[i] / 3;
    const c2x = pts[i + 1][0] - dx[i] / 3;
    const c2y = pts[i + 1][1] - m[i + 1] * dx[i] / 3;
    d += ` C${c1x.toFixed(1)},${c1y.toFixed(1)} ${c2x.toFixed(1)},${c2y.toFixed(1)} ${pts[i + 1][0].toFixed(1)},${pts[i + 1][1].toFixed(1)}`;
  }
  return d;
}

function smoothPath(pts: Pt[]): string {
  if (pts.length === 0) return "";
  return `M${pts[0][0].toFixed(1)},${pts[0][1].toFixed(1)}${smoothSegments(pts)}`;
}

function LegendDot({ color, label }: { color: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1">
      <span className="inline-block size-2 rounded-full" style={{ background: color }} />
      {label}
    </span>
  );
}

export function TokenChart({
  data,
  preset,
  formatY = formatTokens,
}: {
  data: UsageSeries;
  preset: string;
  formatY?: (n: number) => string;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [hover, setHover] = useState<number | null>(null);
  const [pos, setPos] = useState<{ x: number; y: number } | null>(null);

  const { buckets, total, models } = data;
  const modelNames = Object.keys(models);
  const n = buckets.length;

  if (n === 0) {
    return <div className="flex h-[160px] items-center justify-center text-sm text-muted-foreground">暂无数据</div>;
  }

  const visibleNames = modelNames.filter((m) => !hidden.has(m));
  const max = Math.max(1, ...total); // total ≥ any single model, so it's the ceiling
  const colorOf = (m: string) => MODEL_COLORS[modelNames.indexOf(m) % MODEL_COLORS.length];

  const xAt = (i: number) => (n === 1 ? PAD.l + PLOT_W / 2 : PAD.l + (i / (n - 1)) * PLOT_W);
  const yAt = (v: number) => PAD.t + PLOT_H - (v / max) * PLOT_H;
  const pts = (series: number[]): Pt[] => series.map((v, i) => [xAt(i), yAt(v)]);

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
        {modelNames.map((m) => (
          <button key={m} type="button" onClick={() => toggle(m)} className={hidden.has(m) ? "opacity-40" : ""}>
            <LegendDot color={colorOf(m)} label={m} />
          </button>
        ))}
      </div>

      <svg viewBox={`0 0 ${W} ${H}`} width="100%" onMouseMove={onMove} onMouseLeave={onLeave}>
        {/* gridlines + y labels */}
        {yTicks.map((t, i) => (
          <g key={i}>
            <line x1={PAD.l} y1={t.y} x2={W - PAD.r} y2={t.y} stroke="currentColor" strokeOpacity={i === 4 ? 0.3 : 0.12} />
            <text x={PAD.l - 6} y={t.y + 3} textAnchor="end" fontSize="10" fill="currentColor">{formatY(t.v)}</text>
          </g>
        ))}
        {/* x labels */}
        {xLabelIdx.map((i) => (
          <text key={i} x={xAt(i)} y={H - 8} textAnchor="middle" fontSize="10" fill="currentColor">{fmtTs(buckets[i], preset)}</text>
        ))}

        {/* total area + line (primary) */}
        <g className="text-primary">
          <path d={`${smoothPath(pts(total))} L${xAt(n - 1).toFixed(1)},${yAt(0).toFixed(1)} L${xAt(0).toFixed(1)},${yAt(0).toFixed(1)} Z`} fill="currentColor" fillOpacity={0.12} />
          <path d={smoothPath(pts(total))} fill="none" stroke="currentColor" strokeWidth={1.5} vectorEffect="non-scaling-stroke" strokeLinejoin="round" strokeLinecap="round" />
        </g>
        {/* per-model lines */}
        {visibleNames.map((m) => (
          <path key={m} d={smoothPath(pts(models[m]))} fill="none" stroke={colorOf(m)} strokeWidth={1.25} vectorEffect="non-scaling-stroke" strokeLinejoin="round" strokeLinecap="round" />
        ))}

        {/* hover guide + total point */}
        {hover !== null && (
          <>
            <line x1={xAt(hover)} y1={PAD.t} x2={xAt(hover)} y2={PAD.t + PLOT_H} stroke="currentColor" strokeOpacity={0.35} strokeDasharray="3 3" />
            <circle cx={xAt(hover)} cy={yAt(total[hover])} r={3} fill="var(--color-primary)" />
          </>
        )}
      </svg>

      {/* cursor-following tooltip */}
      {hover !== null && pos !== null && (
        <div className="pointer-events-none absolute z-10 min-w-[120px] rounded-md border border-border bg-card px-2 py-1 text-xs shadow-sm" style={{ left: pos.x + 14, top: pos.y + 14 }}>
          <div className="mb-0.5 text-foreground">{fmtTs(buckets[hover], preset)}</div>
          <div>
            总量 <span className="text-foreground">{formatY(total[hover])}</span>
          </div>
          {visibleNames.map((m) => (
            <div key={m} style={{ color: colorOf(m) }}>
              {m} {formatY(models[m][hover])}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
