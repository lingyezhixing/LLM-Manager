import type { useLogViewer } from "@/lib/hooks/use-model-logs";

/** 级别过滤 + 搜索跳转 + 窗口化行渲染 + 返回最新药丸——ModelLogPanel 与 LogViewer 共享核心。
 * 头部(标题/会话信息)由两消费方各自渲染;h 来自 useLogViewer(useModelLogs/useSessionLogs 返回同形)。
 * 命名 LOG_LEVEL_FILTERS 以区别于 general-panel 的 LOG_LEVELS(后者为后端大写级别,语义不同)。 */
const LOG_LEVEL_FILTERS = ["", "info", "ok", "warn", "error"] as const;   // "" = 全部
const LOG_LEVEL_FILTER_LABEL: Record<string, string> = {
  "": "全部", info: "info", ok: "ok", warn: "warn", error: "error",
};
const LOG_LINE_LEVEL_COLOR: Record<string, string> = {
  info: "text-foreground/80", ok: "text-success-accent", warn: "text-warning-accent", error: "text-destructive-accent",
};

type LogLinesView = ReturnType<typeof useLogViewer>;

/** 过滤/搜索条 + 行区。h.mode 为 history 时显示返回最新药丸;无匹配仅在 hasSearched 后显示。 */
export function LogLines({ h }: { h: LogLinesView }) {
  const { level, setLevel } = h;
  const showJump = h.mode === "history" || h.newCount > 0;
  const jumpLabel = h.mode === "history"
    ? `← 返回最新${h.newCount ? ` (${h.newCount})` : ""}`
    : `↓ ${h.newCount} 条新日志`;

  return (
    <>
      <div className="flex flex-wrap items-center gap-2 border-b border-border bg-muted/30 px-3.5 py-1.5 text-dense">
        {LOG_LEVEL_FILTERS.map((lv) => (
          <button key={lv} onClick={() => setLevel(lv)}
            className={`rounded border px-2 py-0.5 transition-colors ${level === lv ? "border-transparent bg-primary-accent/12 font-medium text-primary-accent" : "border-border bg-card text-muted-foreground hover:text-foreground"}`}>
            {LOG_LEVEL_FILTER_LABEL[lv]}
          </button>
        ))}
        <span className="mx-1 h-3 w-px bg-border" />
        <input
          value={h.input}
          onChange={(e) => h.onInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") h.runSearch(h.input);
            if (e.key === "Escape") h.onInput("");
          }}
          placeholder="搜索本次日志…(Enter 搜索)"
          className="w-44 rounded border border-border bg-background px-2 py-0.5 text-foreground placeholder:text-muted-foreground/60"
        />
        {h.searching ? <span className="text-muted-foreground">…</span>
          : h.matches.length > 0 ? (
            <div className="flex items-center gap-1">
              <button onClick={h.prevMatch} className="rounded border border-border bg-card px-1.5 py-0.5 text-muted-foreground hover:text-foreground">‹</button>
              <span className="text-muted-foreground tabular-nums">{h.matchIdx + 1}/{h.matches.length}</span>
              <button onClick={h.nextMatch} className="rounded border border-border bg-card px-1.5 py-0.5 text-muted-foreground hover:text-foreground">›</button>
              {h.matchTotal > h.matches.length && (
                <span className="text-muted-foreground/70" title="后端硬限 500 条,超出部分不可跳转">
                  共{h.matchTotal}条
                </span>
              )}
            </div>
          ) : h.hasSearched ? <span className="text-muted-foreground">无匹配</span> : null}
      </div>
      <div ref={h.scroller} onScroll={h.onScroll}
        className="relative flex-1 overflow-auto bg-background p-3 font-mono text-ui leading-relaxed">
        {h.atOldest && <div className="py-1 text-center text-micro text-muted-foreground/60">已加载最早</div>}
        {h.displayed.map((l) => {
          const isMatch = h.matchSet.has(l.id);
          const isCurrent = h.currentMatch === l.id;
          return (
            <div key={l.id} data-line-id={l.id}
              className={`${LOG_LINE_LEVEL_COLOR[l.level]} -mx-1 whitespace-nowrap rounded px-1 ${isCurrent ? "bg-warning/30 ring-1 ring-warning" : isMatch ? "bg-warning/10" : ""}`}>
              <span className="text-muted-foreground/50">{new Date(l.ts * 1000).toLocaleTimeString("zh-CN", { hour12: false })} </span>
              {l.text}
            </div>
          );
        })}
        {showJump && (
          <button onClick={h.backToLive}
            className="absolute bottom-3 right-3.5 rounded-full bg-primary px-3 py-1 text-dense font-medium text-primary-foreground shadow-card hover:opacity-90">
            {jumpLabel}
          </button>
        )}
      </div>
    </>
  );
}
