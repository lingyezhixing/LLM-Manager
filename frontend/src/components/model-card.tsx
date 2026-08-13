import type { ModelInfo } from "@/lib/api";
import { startModel, stopModel } from "@/lib/api";
import { errMsg, idleText, pendingLabel } from "@/lib/format";
import { useToast } from "@/lib/hooks/use-toast";

// 状态点用主题 token 的 CSS 变量(success/primary-accent/destructive/muted-foreground),随主题变、不荧光。
const DOT: Record<string, string> = {
  stopped: "var(--color-muted-foreground)",
  starting: "var(--color-primary-accent)",
  init_script: "var(--color-primary-accent)",
  health_check: "var(--color-primary-accent)",
  routing: "var(--color-success)",
  failed: "var(--color-destructive)",
};
const STAGE_LABEL: Record<string, string> = {
  stopped: "已停止", starting: "启动中 · 初始化",
  init_script: "启动中 · 启动脚本", health_check: "启动中 · 健康检查", routing: "运行中", failed: "失败",
};

export function ModelCard({ m, selected, nowMs, onSelect }: {
  m: ModelInfo; selected: boolean; nowMs: number; onSelect: () => void;
}) {
  const toast = useToast();
  const starting = ["starting", "init_script", "health_check"].includes(m.status);
  const routing = m.status === "routing";
  const idle = idleText(m.last_access, nowMs);
  const statusText = routing
    ? `运行中${m.pending === 0 && idle ? ` · ${idle}` : ""}`
    : m.status === "failed" ? `失败${m.failure_reason ? ` · ${m.failure_reason}` : ""}`
    : STAGE_LABEL[m.status] ?? m.status;

  const btn = starting
    ? { label: "中断", cls: "abort", fn: () => stopModel(m.alias) }
    : routing
      ? { label: "停止", cls: "stop", fn: () => stopModel(m.alias) }
      : { label: "启动", cls: "go", fn: () => startModel(m.alias) };

  return (
    <div role="button" tabIndex={0} onClick={onSelect}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onSelect();
        }
      }}
      className={`flex items-center gap-2 rounded-lg border p-2.5 cursor-pointer transition-colors ${
        selected ? "border-primary-accent bg-primary-accent/12" : "border-border-subtle hover:bg-card-hover"}`}>
      <div className="min-w-0 flex-1">
        <div className="text-[12.5px] font-semibold truncate text-foreground">{m.alias}</div>
        <div className="mt-0.5 flex items-center gap-1.5 text-[10.5px]">
          <span className="rounded border border-border/60 px-1.5 py-px text-muted-foreground">{m.mode}</span>
          <span className={m.pending > 0 ? "text-warning font-semibold" : "text-muted-foreground"}>
            {m.pending > 0 ? `● ${pendingLabel(m.pending)}` : "0 请求"}
          </span>
        </div>
        <div className="mt-0.5 text-[10.5px] text-muted-foreground">
          <span className="mr-1 inline-block size-[7px] rounded-full align-middle" style={{ background: DOT[m.status] ?? "var(--color-muted-foreground)" }} />
          {statusText}
        </div>
      </div>
      <button onClick={(e) => { e.stopPropagation(); btn.fn().catch((err: unknown) => toast.error(errMsg(err))); }}
        className={`shrink-0 rounded-md border px-3 py-1.5 text-[10.5px] font-medium transition-colors ${
          btn.cls === "go" ? "border-primary bg-primary text-primary-foreground hover:opacity-90"
          : btn.cls === "stop" ? "border-destructive bg-destructive text-destructive-foreground hover:opacity-90"
          : "border-warning bg-warning text-warning-foreground hover:opacity-90"}`}>
        {btn.label}
      </button>
    </div>
  );
}
