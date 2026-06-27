import type { ModelInfo } from "@/lib/api";
import { startModel, stopModel } from "@/lib/api";

const DOT: Record<string, string> = {
  stopped: "#888", starting: "#3b82f6", init_script: "#3b82f6",
  health_check: "#3b82f6", routing: "#22c55e", failed: "#ef4444",
};
const STAGE_LABEL: Record<string, string> = {
  stopped: "已停止", starting: "启动中 · 初始化",
  init_script: "启动中 · 启动脚本", health_check: "启动中 · 健康检查", routing: "运行中", failed: "失败",
};

function formatIdle(sec: number): string {
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  if (h > 0) return `${h}h ${m}m ${s}s`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}

export function ModelCard({ m, selected, nowMs, onSelect }: {
  m: ModelInfo; selected: boolean; nowMs: number; onSelect: () => void;
}) {
  const starting = ["starting", "init_script", "health_check"].includes(m.status);
  const routing = m.status === "routing";
  const idleSec = m.last_access > 0 ? Math.max(0, Math.floor((nowMs - m.last_access * 1000) / 1000)) : 0;
  const statusText = routing
    ? `运行中${m.pending === 0 ? ` · 空闲 ${formatIdle(idleSec)}` : ""}`
    : m.status === "failed" ? `失败${m.failure_reason ? ` · ${m.failure_reason}` : ""}`
    : STAGE_LABEL[m.status] ?? m.status;

  const btn = starting
    ? { label: "中断", cls: "abort", fn: () => stopModel(m.alias) }
    : routing
      ? { label: "停止", cls: "stop", fn: () => stopModel(m.alias) }
      : { label: "启动", cls: "go", fn: () => startModel(m.alias) };

  return (
    <div onClick={onSelect}
      className={`flex items-center gap-2 rounded-lg border p-2.5 cursor-pointer ${
        selected ? "border-primary bg-primary/10" : "border-border hover:bg-muted/40"}`}>
      <div className="min-w-0 flex-1">
        <div className="text-[12.5px] font-semibold truncate">{m.alias}</div>
        <div className="mt-0.5 flex items-center gap-1.5 text-[10.5px]">
          <span className="rounded border border-border/60 px-1.5 py-px text-muted-foreground">{m.mode}</span>
          <span className={m.pending > 0 ? "text-yellow-500 font-semibold" : "text-muted-foreground"}>
            {m.pending > 0 ? `● ${m.pending} 请求中` : "0 请求"}
          </span>
        </div>
        <div className="mt-0.5 text-[10.5px] text-muted-foreground">
          <span className="mr-1 inline-block size-[7px] rounded-full align-middle" style={{ background: DOT[m.status] ?? "#888" }} />
          {statusText}
        </div>
      </div>
      <button onClick={(e) => { e.stopPropagation(); btn.fn(); }}
        className={`shrink-0 rounded-md border px-3 py-1.5 text-[10.5px] font-medium ${
          btn.cls === "go" ? "border-primary bg-primary text-primary-foreground"
          : btn.cls === "stop" ? "border-destructive bg-destructive/20 text-red-300"
          : "border-yellow-500 bg-yellow-500/20 text-yellow-200"}`}>
        {btn.label}
      </button>
    </div>
  );
}
