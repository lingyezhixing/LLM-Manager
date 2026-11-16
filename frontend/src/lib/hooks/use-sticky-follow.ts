import { useCallback, useEffect, useRef, useState, type RefObject } from "react";

/** 贴底判定阈值(px):内容底部距视口底部 < 该值即视为贴底(日志查看器/请求详情共用)。 */
export const STICKY_THRESHOLD = 24;

/** 贴底判定(纯函数):gap = scrollHeight - scrollTop - clientHeight,gap < threshold 视为贴底。 */
export const isAtBottom = (
  el: { scrollHeight: number; scrollTop: number; clientHeight: number },
  threshold: number = STICKY_THRESHOLD,
): boolean => el.scrollHeight - el.scrollTop - el.clientHeight < threshold;

/**
 * 滚动跟随(贴底)共享 hook:「贴底自动跟进 / 上滚暂停 / 跳底恢复」统一状态机。
 * following 由外部持有(reducer 或 useState),本 hook 只管 DOM:
 *  - autoFollow + following 且 contentVersion(内容)变化 → 贴底;
 *  - 滚动事件判定 atBottom(供调用方「不在底部就显示跳底药丸」),值翻转才回调 onFollowChange;
 *  - jumpToBottom:回底 + 恢复跟随(调用方按需附加自己的清理,如清 newCount)。
 * autoFollow=false 用于静态内容:只取 atBottom/jumpToBottom,不触碰 following。
 */
export interface UseStickyFollowOptions {
  scroller: RefObject<HTMLElement | null>;
  /** 随内容增长而变化的值(行数组/块数组);变化且 following 时重新贴底。 */
  contentVersion: unknown;
  following: boolean;
  /** 用户滚动导致跟随态翻转时回调(仅 autoFollow 且值变化时调用)。 */
  onFollowChange: (v: boolean) => void;
  /** 每次滚动的附加回调(元素 + atBottom);latest-ref 持有,身份变化不重建 onScroll。 */
  onScroll?: (el: HTMLElement, atBottom: boolean) => void;
  /** 是否启用自动跟随(默认 true;静态内容传 false)。 */
  autoFollow?: boolean;
}

export function useStickyFollow({
  scroller,
  contentVersion,
  following,
  onFollowChange,
  onScroll,
  autoFollow = true,
}: UseStickyFollowOptions) {
  const [atBottom, setAtBottom] = useState(true);
  // latest-ref:回调可捕获最新闭包(如日志的 onScroll 依赖 loadMoreAbove),onScroll 身份保持稳定。
  const onFollowChangeRef = useRef(onFollowChange);
  const onScrollRef = useRef(onScroll);
  useEffect(() => {
    onFollowChangeRef.current = onFollowChange;
    onScrollRef.current = onScroll;
  });

  // 跟进:内容增长或 following 置位 → 贴底(用户手动滚回底部时 ≤24px 的微移,与既有行为一致)。
  useEffect(() => {
    if (!autoFollow || !following) return;
    const el = scroller.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [contentVersion, following, autoFollow, scroller]);

  // 内容变化/元素挂载后复测 atBottom:防「初始 atBottom=true」在溢出挂载时失准
  // (如请求输入区展开即超长)。定义在跟进 effect 之后 → 同一 flush 内先贴底后测量。
  useEffect(() => {
    const el = scroller.current;
    if (!el) return;
    const b = isAtBottom(el);
    setAtBottom((prev) => (prev === b ? prev : b));
  }, [scroller, contentVersion]);

  const handleScroll = useCallback(() => {
    const el = scroller.current;
    if (!el) return;
    const atB = isAtBottom(el);
    setAtBottom((prev) => (prev === atB ? prev : atB));
    if (autoFollow && atB !== following) onFollowChangeRef.current(atB);
    onScrollRef.current?.(el, atB);
  }, [scroller, following, autoFollow]);

  const jumpToBottom = useCallback(() => {
    const el = scroller.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
    setAtBottom(true);
    if (autoFollow) onFollowChangeRef.current(true);
  }, [scroller, autoFollow]);

  return { atBottom, onScroll: handleScroll, jumpToBottom };
}
