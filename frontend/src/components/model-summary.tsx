import { Link } from "react-router-dom";
import { Activity } from "lucide-react";
import { Card, Loading } from "@/components/ui/card";
import { InfoTile } from "@/components/ui/info-tile";
import { formatIdle } from "@/lib/format";
import { useEventStream } from "@/lib/hooks/use-event-stream";
import { useNowTick } from "@/lib/hooks/use-now-tick";
import type { ModelInfo, ModelsResponse } from "@/lib/api";

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
 * ticks idle locally — no polling. Card w/ icon header + 3 KPI tiles + running rows.
 */
export function ModelSummary() {
  const { data, error } = useEventStream<ModelsResponse>("/api/models/stream");
  const now = useNowTick(1000);

  if (error) return <p className="text-sm text-muted-foreground">模型数据加载失败(后端未连接,将自动重试)</p>;
  if (!data) return <Loading />;

  const models: ModelInfo[] = data.data ?? [];
  const running = models.filter((m) => m.status === "routing")
    .sort((a, b) => a.port - b.port);   // 统一按 port 升序
  const stopped = models.filter((m) => m.status === "stopped");
  const failed = models.filter((m) => m.status === "failed");

  return (
    <Card>
      <div className="mb-3 flex items-center justify-between">
        <h3 className="flex items-center gap-2 text-sm font-semibold text-foreground">
          <Activity className="size-4 text-primary-accent" />
          模型摘要
        </h3>
        <Link to="/models" className="text-xs text-primary-accent hover:underline">管理全部 →</Link>
      </div>
      <div className="mb-3 grid grid-cols-3 gap-2">
        <InfoTile label="运行中" value={running.length} valueClass="text-success-accent" />
        <InfoTile label="已停止" value={stopped.length} />
        <InfoTile label="失败" value={failed.length} valueClass="text-destructive-accent" />
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
                className="flex items-center justify-between rounded-md border border-border-subtle px-3 py-2 text-sm"
              >
                <span className="flex items-center gap-2 truncate">
                  <span className="size-1.5 shrink-0 rounded-full bg-success" />
                  <span className="truncate">{m.alias}</span>
                </span>
                <span className="font-mono text-xs text-muted-foreground">
                  端口 {m.port}{extra ? ` · ${extra}` : ""}
                </span>
              </div>
            );
          })}
        </div>
      )}
    </Card>
  );
}
