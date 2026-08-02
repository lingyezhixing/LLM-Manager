import { useState } from "react";
import { useSessionLogs } from "@/lib/use-model-logs";
import type { LogSession } from "@/lib/api";

const LEVELS = ["", "info", "ok", "warn", "error"] as const;
const LEVEL_LABEL: Record<string, string> = { "": "全部", info: "info", ok: "ok", warn: "warn", error: "error" };
const COLOR: Record<string, string> = {
  info: "text-foreground/80", ok: "text-success", warn: "text-warning", error: "text-destructive",
};

/** 右栏日志行详情:级别过滤 + 搜索跳转 + 实时跟随(ModelLogPanel 同款交互)。 */
export function LogViewer({ session }: { session: LogSession }) {
  const [level, setLevel] = useState<string>("");
  const [input, setInput] = useState("");
  const h = useSessionLogs(session.id, level);   // key=session.id 由父级保证重建
  const showJump = h.mode === "history" || h.newCount > 0;
  const jumpLabel = h.mode === "history"
    ? `← 返回最新${h.newCount ? ` (${h.newCount})` : ""}`
    : `↓ ${h.newCount} 条新日志`;

  return (
    <div className="flex h-full flex-col overflow-hidden rounded-lg border border-border bg-card">
      <div className="flex items-center justify-between border-b border-border px-3.5 py-2">
        <span className="text-[12px] font-semibold text-foreground">
          {session.alias ?? "系统日志"}
          <span className="ml-2 text-[10.5px] font-normal text-muted-foreground">
            #{session.id} · {session.status === "running" ? "进行中" : "已结束"} · {session.line_count} 行
          </span>
        </span>
      </div>
      {/* 过滤/搜索条:与 ModelLogPanel 相同结构(LEVELS 按钮 + 输入框 + ‹N/M› + 无匹配态) */}
      <div className="flex flex-wrap items-center gap-2 border-b border-border bg-muted/30 px-3.5 py-1.5 text-[10.5px]">
        {LEVELS.map((lv) => (
          <button key={lv} onClick={() => setLevel(lv)}
            className={`rounded border px-2 py-0.5 transition-colors ${level === lv ? "border-primary bg-primary text-primary-foreground" : "border-border bg-card text-muted-foreground hover:text-foreground"}`}>
            {LEVEL_LABEL[lv]}
          </button>
        ))}
        <span className="mx-1 h-3 w-px bg-border" />
        <input
          value={input}
          onChange={(e) => { setInput(e.target.value); h.onInputChange(); }}
          onKeyDown={(e) => {
            if (e.key === "Enter") h.runSearch(input);
            if (e.key === "Escape") { setInput(""); h.onInputChange(); }
          }}
          placeholder="搜索本次日志…(Enter 搜索)"
          className="w-44 rounded border border-border bg-background px-2 py-0.5 text-foreground placeholder:text-muted-foreground/60 focus:border-primary focus:outline-none"
        />
        {h.searching ? <span className="text-muted-foreground">…</span>
          : h.matches.length > 0 ? (
            <div className="flex items-center gap-1">
              <button onClick={h.prevMatch} className="rounded border border-border bg-card px-1.5 py-0.5 text-muted-foreground hover:text-foreground">‹</button>
              <span className="text-muted-foreground tabular-nums">{h.matchIdx + 1}/{h.matches.length}</span>
              <button onClick={h.nextMatch} className="rounded border border-border bg-card px-1.5 py-0.5 text-muted-foreground hover:text-foreground">›</button>
            </div>
          ) : h.hasSearched ? <span className="text-muted-foreground">无匹配</span> : null}
      </div>
      {/* 行渲染:同 ModelLogPanel(scroller/onScroll/displayed/匹配高亮/跳转药丸) */}
      <div ref={h.scroller} onScroll={h.onScroll}
        className="relative flex-1 overflow-auto bg-background p-3 font-mono text-[11px] leading-relaxed">
        {h.atOldest && <div className="py-1 text-center text-[10px] text-muted-foreground/60">已加载最早</div>}
        {h.displayed.map((l) => {
          const isMatch = h.matchSet.has(l.id);
          const isCurrent = h.currentMatch === l.id;
          return (
            <div key={l.id} data-line-id={l.id}
              className={`${COLOR[l.level]} -mx-1 whitespace-nowrap rounded px-1 ${isCurrent ? "bg-warning/30 ring-1 ring-warning" : isMatch ? "bg-warning/10" : ""}`}>
              <span className="text-muted-foreground/50">{new Date(l.ts * 1000).toLocaleTimeString("zh-CN", { hour12: false })} </span>
              {l.text}
            </div>
          );
        })}
        {showJump && (
          <button onClick={h.backToLive}
            className="absolute bottom-3 right-3.5 rounded-full bg-primary px-3 py-1 text-[10.5px] font-medium text-primary-foreground shadow-lg hover:opacity-90">
            {jumpLabel}
          </button>
        )}
      </div>
    </div>
  );
}
