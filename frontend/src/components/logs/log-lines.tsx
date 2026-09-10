import type { useLogViewer } from "@/lib/hooks/use-model-logs";
import { useBlockWindow } from "@/lib/hooks/use-block-window";
import { memo, useMemo, useRef, useEffect } from "react";

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

/** 单行渲染(memo 避免块级虚拟化重渲染)。pre-wrap + break-all:超长行自动折行,
 * 不允许页面向右膨胀(容器列 minmax(0,1fr) 已约束,双保险)。
 * flex + 时间戳 shrink-0:换行后继续文本从时间戳右侧开始(悬挂缩进),行从属清晰。 */
const LogRow = memo<{ l: { id: number; ts: number; text: string; level: string }; isMatch: boolean; isCurrent: boolean }>(
  ({ l, isMatch, isCurrent }) => (
    <div data-line-id={l.id}
      className={`${LOG_LINE_LEVEL_COLOR[l.level]} -mx-1 flex gap-1.5 rounded px-1 ${isCurrent ? "bg-warning/30 ring-1 ring-warning" : isMatch ? "bg-warning/10" : ""}`}>
      <span className="shrink-0 tabular-nums text-muted-foreground/50">
        {new Date(l.ts * 1000).toLocaleTimeString("zh-CN", { hour12: false })}
      </span>
      <span className="min-w-0 flex-1 whitespace-pre-wrap break-all">{l.text}</span>
    </div>
  ),
);
LogRow.displayName = "LogRow";

/** 过滤/搜索条 + 行区。h.mode 为 history 时显示返回最新药丸;无匹配仅在 hasSearched 后显示。 */
export function LogLines({ h }: { h: LogLinesView }) {
  const { level, setLevel } = h;
  const { visible, blockOf, mount, nBlocks, BLOCK, heights, EST_ROW_H } = useBlockWindow(h.displayed.length, h.scroller);

  // 搜索跳转的块挂载:目标行(h.currentMatch)所在块未挂载时强制挂载。挂载 setState 的重渲染
  // 在本轮 effect flush 之后才提交,而 hook 的 scrollTargetId 定位 effect 在同一 flush 内已跑
  // (querySelector 查不到 → 放弃)——故下方 pendingScroll 补一次滚动。
  useEffect(() => {
    if (h.currentMatch == null) return;
    const idx = h.displayed.findIndex((l) => l.id === h.currentMatch);
    if (idx >= 0) {
      const b = blockOf(idx);
      if (!visible.has(b)) mount(b);
    }
  }, [h.displayed, h.currentMatch, visible, blockOf, mount]);

  // 补滚动:跳转目标所在块挂载完成(visible 变化)后滚到目标行。hook 的 scrollTargetId
  // 定位 effect 在块挂载提交前跑、查不到元素即放弃——无论 live(数据在窗口内但块未挂载)
  // 还是 history 模式都会漏滚,这里统一补。stale 计数:两次 displayed 变化内仍未消费即
  // 作废,防陈旧目标日后块被滚入视口时突兀拽视图;「返回最新」由 hook 的 follow effect
  // (父组件,后于本 effect 执行)滚到底,不受影响。
  const pendingScroll = useRef<number | null>(null);
  const pendingStale = useRef(0);
  useEffect(() => {
    pendingScroll.current = h.currentMatch != null ? h.currentMatch : null; // 搜索被清(输入变化)→ 取消
    pendingStale.current = 0;
  }, [h.currentMatch]);
  useEffect(() => {
    const target = pendingScroll.current;
    if (target == null || !h.scroller.current) return;
    const el = h.scroller.current.querySelector(`[data-line-id="${target}"]`);
    if (el) {
      (el as HTMLElement).scrollIntoView({ block: "center" });
      pendingScroll.current = null;
    } else if (++pendingStale.current > 2) {
      pendingScroll.current = null; // 迟迟不可达(块已滚出/数据被清)→ 作废
    }
  }, [visible, h.displayed, h.scroller]);

  const showJump = h.mode === "history" || h.newCount > 0;
  const jumpLabel = h.mode === "history"
    ? `← 返回最新${h.newCount ? ` (${h.newCount})` : ""}`
    : `↓ ${h.newCount} 条新日志`;

  // 渲染块列表
  const blocks = useMemo(() => {
    const result: React.ReactNode[] = [];
    // 动态平均行高:折行后实际行高 > EST_ROW_H(20px),固定估算在长行会话里占位
    // 高度严重偏低;已测块的真实高度/行数作全局均值,未测块按均值估(折行修正)。
    let sumH = 0, sumN = 0;
    for (let b = 0; b < nBlocks; b++) {
      const start = b * BLOCK;
      const end = Math.min(start + BLOCK, h.displayed.length);
      const count = end - start;
      if (count === 0) continue;
      const measured = heights.current.get(b);
      if (measured) { sumH += measured; sumN += count; }
    }
    const estRowH = sumN > 0 ? sumH / sumN : EST_ROW_H;

    for (let b = 0; b < nBlocks; b++) {
      const start = b * BLOCK;
      const end = Math.min(start + BLOCK, h.displayed.length);
      const count = end - start;
      if (count === 0) continue;

      if (visible.has(b)) {
        // 真实行块:挂载并测高
        const blockLines = h.displayed.slice(start, end);
        result.push(
          <div key={b} data-block={b} ref={(el) => { if (el) heights.current.set(b, el.offsetHeight); }}>
            {blockLines.map((l) => {
              const isMatch = h.matchSet.has(l.id);
              const isCurrent = h.currentMatch === l.id;
              return <LogRow key={l.id} l={l} isMatch={isMatch} isCurrent={isCurrent} />;
            })}
          </div>
        );
      } else {
        // 占位块:已测高度优先,否则按动态均值估
        const placeholderHeight = heights.current.get(b) ?? count * estRowH;
        result.push(
          <div key={b} data-block={b} style={{ height: `${placeholderHeight}px` }} />
        );
      }
    }
    return result;
  }, [h.displayed, visible, nBlocks, BLOCK, heights, EST_ROW_H, h.matchSet, h.currentMatch]);

  return (
    <>
      <div className="flex flex-wrap items-center gap-2 border-b border-border-subtle bg-muted/30 px-3.5 py-1.5 text-dense">
        {LOG_LEVEL_FILTERS.map((lv) => (
          <button key={lv} onClick={() => setLevel(lv)} aria-pressed={level === lv}
            className={`rounded-full px-2.5 py-0.5 text-ui transition-colors duration-(--motion-base) ${level === lv ? "bg-primary-accent/12 font-medium text-primary-accent" : "text-muted-foreground hover:text-foreground"}`}>
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
          className="w-44 rounded-md border border-border bg-input px-2 py-0.5 text-foreground placeholder:text-muted-foreground/60"
        />
        {h.searching ? <span className="text-muted-foreground">…</span>
          : h.matches.length > 0 ? (
            <div className="flex items-center gap-1">
              <button onClick={h.prevMatch} className="rounded-md border border-border bg-card px-1.5 py-0.5 text-muted-foreground transition-colors duration-(--motion-base) hover:bg-card-hover">‹</button>
              <span className="font-mono text-muted-foreground tabular-nums">{h.matchIdx + 1}/{h.matches.length}</span>
              <button onClick={h.nextMatch} className="rounded-md border border-border bg-card px-1.5 py-0.5 text-muted-foreground transition-colors duration-(--motion-base) hover:bg-card-hover">›</button>
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
        {h.displayed.length === 0 && (
          <div className="py-4 text-center text-xs text-muted-foreground">暂无日志</div>
        )}
        {blocks}
        {showJump && (
          <button onClick={h.backToLive}
            className="absolute bottom-2.5 right-2 z-20 rounded-full bg-primary px-3 py-1 text-dense font-medium text-primary-foreground shadow-card transition-colors duration-(--motion-fast) hover:bg-primary-600">
            {jumpLabel}
          </button>
        )}
      </div>
    </>
  );
}
