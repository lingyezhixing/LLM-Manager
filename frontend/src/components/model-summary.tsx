import { Link } from "react-router-dom";
import { useEventStream } from "@/lib/use-event-stream";
import { useNowTick } from "@/lib/use-now-tick";
import type { ModelInfo, ModelsResponse } from "@/lib/api";

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="flex-1 rounded-lg border border-border px-3 py-2">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="text-lg font-semibold">{value}</div>
    </div>
  );
}

/** Idle duration, precise to seconds, largest unit hours: 45s / 2m 5s / 1h 2m 30s. */
function formatIdle(sec: number): string {
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  if (h > 0) return `${h}h ${m}m ${s}s`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}

/** Per-model trailing status line, with idle ticked locally from last_access. */
function activityText(m: ModelInfo, nowMs: number): string {
  if (m.pending > 0) return `${m.pending} 请求中`;
  if (m.last_access > 0) {
    const idleSec = Math.max(0, Math.floor((nowMs - m.last_access * 1000) / 1000));
    return `空闲 ${formatIdle(idleSec)}`;
  }
  return "";
}

/**
 * Model status summary (概览). Subscribes to /api/models/stream (event-driven push) and
 * ticks idle locally — no polling. Status changes arrive as SSE; idle seconds are derived
 * client-side from last_access.
 */
export function ModelSummary() {
  const data = useEventStream<ModelsResponse>("/api/models/stream");
  const now = useNowTick(1000);

  if (!data) return <p className="text-sm text-muted-foreground">加载中…</p>;

  const models: ModelInfo[] = data.data ?? [];
  const running = models.filter((m) => m.status === "routing");
  const stopped = models.filter((m) => m.status === "stopped");
  const failed = models.filter((m) => m.status === "failed");

  return (
    <div>
      <div className="mb-3 flex gap-3">
        <Stat label="运行中" value={running.length} />
        <Stat label="已停止" value={stopped.length} />
        <Stat label="失败" value={failed.length} />
      </div>
      <div className="mb-2 flex items-center justify-between">
        <span className="text-xs uppercase tracking-wide text-muted-foreground">运行中的模型</span>
        <Link to="/models" className="text-xs text-primary hover:underline">管理全部 →</Link>
      </div>
      {running.length === 0 ? (
        <p className="text-sm text-muted-foreground">无运行中的模型</p>
      ) : (
        <div className="flex flex-col gap-2">
          {running.map((m) => {
            const extra = activityText(m, now);
            return (
              <div
                key={m.alias}
                className="flex items-center justify-between rounded-md border border-border px-3 py-2 text-sm"
              >
                <span className="truncate">{m.alias}</span>
                <span className="text-muted-foreground">端口 {m.port}{extra ? ` · ${extra}` : ""}</span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
