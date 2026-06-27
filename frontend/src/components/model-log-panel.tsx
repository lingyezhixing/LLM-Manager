import { useState } from "react";
import type { ModelInfo } from "@/lib/api";
import { useModelLogs } from "@/lib/use-model-logs";

const LEVELS = ["全部", "info", "warn", "error"] as const;
const COLOR: Record<string, string> = {
  info: "text-muted-foreground", ok: "text-green-500", warn: "text-yellow-500", error: "text-red-500",
};

/** 右栏日志面板(选中模型)。头(名/状态/端口·pid/行数)+ 级别过滤 + 窗口化日志流 + 新日志药丸。 */
export function ModelLogPanel({ m }: { m: ModelInfo }) {
  const [level, setLevel] = useState<string>("全部");
  const { lines, newCount, scroller, onScroll, jumpBottom } = useModelLogs(m.alias);
  const shown = level === "全部" ? lines : lines.filter((l) => l.level === level);
  const routing = m.status === "routing";

  return (
    <div className="flex h-full flex-col overflow-hidden rounded-lg border border-border">
      <div className="flex items-center justify-between border-b border-border px-3.5 py-2">
        <span className="text-[12px] font-semibold">
          <span className="mr-1 inline-block size-[7px] rounded-full align-middle"
            style={{ background: routing ? "#22c55e" : "#888" }} />
          {m.alias}
          <span className="ml-2 text-[10.5px] text-muted-foreground font-normal">
            {m.mode} · :{m.port} · pid {m.pid ?? "—"} · 共 {lines.length} 行
          </span>
        </span>
      </div>
      <div className="flex items-center gap-2 border-b border-border bg-muted/20 px-3.5 py-1.5 text-[10.5px]">
        {LEVELS.map((lv) => (
          <button key={lv} onClick={() => setLevel(lv)}
            className={`rounded border px-2 py-0.5 ${level === lv ? "border-primary bg-primary text-primary-foreground" : "border-border text-muted-foreground"}`}>
            {lv}
          </button>
        ))}
        <span className="ml-auto text-muted-foreground">🔍(Phase 2)</span>
      </div>
      <div ref={scroller} onScroll={onScroll}
        className="relative flex-1 overflow-auto bg-black/30 p-3 font-mono text-[11px] leading-relaxed">
        {shown.map((l) => (
          <div key={l.id} className={COLOR[l.level] + " whitespace-nowrap"}>
            <span className="text-muted-foreground/50">{new Date(l.ts * 1000).toLocaleTimeString("zh-CN", { hour12: false })} </span>
            {l.text}
          </div>
        ))}
        {newCount > 0 && (
          <button onClick={jumpBottom}
            className="absolute bottom-3 right-3.5 rounded-full bg-primary px-3 py-1 text-[10.5px] font-medium text-primary-foreground shadow-lg">
            ↓ {newCount} 条新日志
          </button>
        )}
      </div>
    </div>
  );
}
