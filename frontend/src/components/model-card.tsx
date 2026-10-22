import { useEffect, useRef, useState } from "react";
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

export function ModelCard({ m, selected, nowMs, onSelect, onOpenRequests, index = 0 }: {
  m: ModelInfo; selected: boolean; nowMs: number; onSelect: () => void;
  onOpenRequests?: () => void;  // 有在途请求时,计数可点 → 跳到「请求」zone
  index?: number;  // 列表序号:入场 stagger 延迟(cap 8 防长列表拖尾)
}) {
  const toast = useToast();
  const [acting, setActing] = useState(false);  // 启停动作在途(防连点 + 半透明反馈)
  // 状态点只在"真的变化"时弹一下:首次挂载不弹(否则整列卡片会在加载时齐刷刷跳一下)。
  const prevStatus = useRef(m.status);
  const statusChanged = prevStatus.current !== m.status;
  useEffect(() => {
    prevStatus.current = m.status;
  }, [m.status]);
  const starting = ["starting", "init_script", "health_check"].includes(m.status);
  const routing = m.status === "routing";
  const idle = idleText(m.last_access, nowMs);
  const statusText = routing
    ? `运行中${m.pending === 0 && idle ? ` · ${idle}` : ""}`
    : m.status === "failed" ? `失败${m.failure_reason ? ` · ${m.failure_reason}` : ""}`
    : STAGE_LABEL[m.status] ?? m.status;

  // 按钮标签只有三态,不设"启动中…"这类中间文案:请求本身是毫秒级返回,
  // 中间态只会闪一下;真正的进度由卡片状态行(SSE)呈现。acting 仅用于防连点。
  const btn = starting
    ? { label: "中断", cls: "abort", fn: () => stopModel(m.alias) }
    : routing
      ? { label: "停止", cls: "stop", fn: () => stopModel(m.alias) }
      : { label: "启动", cls: "go", fn: () => startModel(m.alias) };

  const onAct = () => {
    if (acting) return;
    setActing(true);
    btn.fn()
      .catch((err: unknown) => toast.error(errMsg(err)))
      .finally(() => setActing(false));
  };

  return (
    <div role="button" tabIndex={0} aria-pressed={selected} onClick={onSelect}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onSelect();
        }
      }}
      className={`animate-card-in flex items-center gap-2 rounded-md border p-2.5 cursor-pointer transition-colors duration-(--motion-base) ${
        selected ? "border-primary-accent bg-primary-accent/12" : "border-border-subtle hover:bg-card-hover"}`}
      style={{ animationDelay: `${Math.min(index, 8) * 24}ms` }}>
      <div className="min-w-0 flex-1">
        <div className="text-card-title font-semibold truncate text-foreground">{m.alias}</div>
        <div className="mt-0.5 flex items-center gap-1.5 text-dense">
          <span className="rounded border border-border/60 px-1.5 py-px text-muted-foreground">{m.mode}</span>
          <span className={m.pending > 0 ? "text-warning font-semibold transition-colors duration-(--motion-base)" : "text-muted-foreground transition-colors duration-(--motion-base)"}>
            {m.pending > 0 && onOpenRequests ? (
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  onOpenRequests();
                }}
                title="查看该模型的请求"
                className="underline decoration-dotted underline-offset-2 hover:text-warning/80"
              >
                ● {pendingLabel(m.pending)}
              </button>
            ) : m.pending > 0 ? (
              `● ${pendingLabel(m.pending)}`
            ) : (
              "0 请求"
            )}
          </span>
        </div>
        <div className="mt-0.5 text-dense text-muted-foreground">
          <span key={m.status} className={`mr-1 inline-block align-middle ${statusChanged ? "animate-status-pop" : ""}`}>
            <span
              className={`inline-block size-[7px] rounded-full transition-colors duration-(--motion-base) ${m.status === "routing" ? "animate-dot-pulse" : ""}`}
              style={{ background: DOT[m.status] ?? "var(--color-muted-foreground)" }}
            />
          </span>
          {statusText}
        </div>
      </div>
      <button onClick={(e) => { e.stopPropagation(); onAct(); }} disabled={acting}
        className={`shrink-0 whitespace-nowrap rounded-md border px-3 py-1.5 text-center text-dense font-medium transition-colors disabled:opacity-60 ${
          btn.cls === "go" ? "border-primary bg-primary text-primary-foreground hover:bg-primary-600"
          : btn.cls === "stop" ? "border-destructive bg-destructive text-destructive-foreground hover:bg-destructive-600"
          : "border-warning bg-warning text-warning-foreground hover:bg-warning-600"}`}>
        {btn.label}
      </button>
    </div>
  );
}
