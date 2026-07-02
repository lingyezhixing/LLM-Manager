import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { LogLine } from "@/lib/api";
import { fetchLogPage, searchLogs } from "@/lib/api";

const WINDOW = 1500;          // 实时尾窗口 + 历史页大小(行数)
const STICKY_THRESHOLD = 24;  // 贴底判定阈值(px)
const TOP_THRESHOLD = 8;      // 触顶判定阈值(px)——触发向上加载历史
const MAX_PREFIX = 5000;      // historyPrefix 上限(防卡顿;超过丢最旧)

/**
 * 单模型日志查看器。两种模式:
 *  - live:订阅 /logs/stream,实时追加(贴底跟进);滚到顶部自动加载更早历史(prepend 到
 *   historyPrefix),滚轮上滚暂停跟进,来新行累加 newCount。
 *  - history:搜索跳转到窗口外的匹配时载入历史页(/logs?before=),静态浏览;实时行仍进 liveLines
 *   (后台),用 newCount 记数;「返回最新」切回 live。
 * 搜索:后端全量检索 → 匹配行 id;‹/› 在匹配间跳转,目标不在当前窗口则翻页载入后滚动定位。
 * hasSearched 跟踪「是否真的执行过搜索」(runSearch 调用过),而非「输入框是否有字」——
 * 输入未按 Enter 时不显示「无匹配」,避免误导。
 * level 为后端查询参数(SSE/搜索/翻页/向上加载均带),变更时重订阅 + 清搜索。
 */
export function useModelLogs(alias: string, level: string) {
  const levelParam = level || undefined;
  const [liveLines, setLiveLines] = useState<LogLine[]>([]);
  const [historyPrefix, setHistoryPrefix] = useState<LogLine[]>([]);     // live 模式顶部加载的历史(旧→新)
  const [historyPage, setHistoryPage] = useState<LogLine[] | null>(null); // 搜索跳转载入的历史页(history 模式)
  const [following, setFollowing] = useState(true);
  const [newCount, setNewCount] = useState(0);
  const [matches, setMatches] = useState<number[]>([]);
  const [matchIdx, setMatchIdx] = useState(-1);
  const [hasSearched, setHasSearched] = useState(false);   // bug1:跟踪搜索执行,而非输入框内容
  const [searching, setSearching] = useState(false);
  const [scrollTargetId, setScrollTargetId] = useState<number | null>(null);
  const [loadingTop, setLoadingTop] = useState(false);     // 向上加载防抖
  const [atOldest, setAtOldest] = useState(false);         // 已加载到最早(id=1)

  const scroller = useRef<HTMLDivElement | null>(null);
  const pendingTopFixRef = useRef<{ h: number; t: number } | null>(null); // prepend 视口维持基准
  const followingRef = useRef(true);
  const liveRef = useRef(true);
  useEffect(() => { followingRef.current = following; }, [following]);
  useEffect(() => { liveRef.current = historyPage === null; }, [historyPage]);

  const liveView = useMemo(() => [...historyPrefix, ...liveLines], [historyPrefix, liveLines]);
  const displayed = historyPage ?? liveView;
  const mode: "live" | "history" = historyPage ? "history" : "live";

  // SSE 实时尾(随 alias/level 重订阅,重置视图 + 搜索)。新行不在「实时+跟进」态则记 newCount。
  useEffect(() => {
    setLiveLines([]); setHistoryPrefix([]); setHistoryPage(null); setFollowing(true); setNewCount(0);
    setMatches([]); setMatchIdx(-1); setHasSearched(false); setAtOldest(false);
    const url = `/api/models/${encodeURIComponent(alias)}/logs/stream${levelParam ? `?level=${levelParam}` : ""}`;
    const es = new EventSource(url);
    es.onmessage = (ev) => {
      try {
        const l = JSON.parse(ev.data) as LogLine;
        setLiveLines((prev) => {
          const next = prev.length >= WINDOW ? prev.slice(prev.length - WINDOW + 1) : prev;
          return [...next, l];
        });
        if (!(liveRef.current && followingRef.current)) setNewCount((n) => n + 1);
      } catch { /* 帧异常忽略 */ }
    };
    return () => es.close();
  }, [alias, levelParam]);

  // 跟进:live + following → 新行贴底。
  useEffect(() => {
    if (mode === "live" && following && scroller.current) {
      scroller.current.scrollTop = scroller.current.scrollHeight;
    }
  }, [liveLines, following, mode]);

  // bug2:prepend 后维持视口位置(DOM 更新后同步调整 scrollTop,在 paint 前)。
  useLayoutEffect(() => {
    const fix = pendingTopFixRef.current;
    const el = scroller.current;
    if (fix && el) {
      el.scrollTop = fix.t + (el.scrollHeight - fix.h);
      pendingTopFixRef.current = null;
    }
  }, [historyPrefix]);

  // 滚动到搜索目标(渲染后)。
  useEffect(() => {
    if (scrollTargetId == null || !scroller.current) return;
    const el = scroller.current.querySelector(`[data-line-id="${scrollTargetId}"]`);
    if (el) (el as HTMLElement).scrollIntoView({ block: "center" });
    setScrollTargetId(null);
  }, [displayed, scrollTargetId]);

  // bug2:向上加载更早日志——prepend 到 historyPrefix,维持视口,防抖,上限,到顶。
  const loadMoreAbove = useCallback(async () => {
    if (loadingTop || mode !== "live") return;
    const firstId = historyPrefix.length > 0 ? historyPrefix[0].id
      : (liveLines.length > 0 ? liveLines[0].id : null);
    if (firstId == null || firstId <= 1) { setAtOldest(true); return; }   // 已到最早(id 从 1 起)
    const el = scroller.current;
    if (el) pendingTopFixRef.current = { h: el.scrollHeight, t: el.scrollTop }; // prepend 前基准
    setLoadingTop(true);
    try {
      const page = await fetchLogPage(alias, firstId, WINDOW, levelParam);
      const newer = page.filter((l) => l.id < firstId);   // 去重(后端返回 id<firstId 的最近 WINDOW 行)
      if (newer.length === 0) { setAtOldest(true); return; }
      setHistoryPrefix((prev) => {
        const merged = [...newer, ...prev];
        return merged.length > MAX_PREFIX ? merged.slice(merged.length - MAX_PREFIX) : merged;
      });
      if (newer[0].id <= 1) setAtOldest(true);             // 加载到最早(id=1)
    } catch { /* best-effort */ } finally {
      setLoadingTop(false);
    }
  }, [alias, levelParam, loadingTop, mode, historyPrefix, liveLines]);

  const onScroll = useCallback(() => {
    if (mode === "history") return;          // 历史页不自动跟进/加载
    const el = scroller.current;
    if (!el) return;
    if (el.scrollTop <= TOP_THRESHOLD) loadMoreAbove();    // bug2:触顶 → 加载更早
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < STICKY_THRESHOLD;
    setFollowing(atBottom);
    if (atBottom) setNewCount(0);
  }, [mode, loadMoreAbove]);

  // 跳到 all[idx]:在当前窗口则滚动定位,否则翻页载入(target 为页底)再定位。
  const jumpToMatch = useCallback((all: number[], idx: number) => {
    if (idx < 0 || idx >= all.length) return;
    const target = all[idx];
    const inView = (historyPage ?? liveView).some((l) => l.id === target);
    if (inView) {
      setScrollTargetId(target);
    } else {
      fetchLogPage(alias, target + 1, WINDOW, levelParam)
        .then((page) => { setHistoryPage(page); setScrollTargetId(target); })
        .catch(() => { /* best-effort */ });
    }
  }, [alias, levelParam, historyPage, liveView]);

  const runSearch = useCallback(async (q: string) => {
    setHasSearched(true);                    // bug1:标记已执行搜索(无论 q 是否空)
    if (!q.trim()) { setMatches([]); setMatchIdx(-1); return; }
    setSearching(true);
    try {
      const res = await searchLogs(alias, q, levelParam);
      setMatches(res.matches);
      const idx = res.matches.length ? 0 : -1;
      setMatchIdx(idx);
      if (idx >= 0) jumpToMatch(res.matches, idx);
    } finally { setSearching(false); }
  }, [alias, levelParam, jumpToMatch]);

  // bug1:用户改输入 → 清上次搜索结果 + hasSearched(回「未搜索」态,不显示「无匹配」)。
  // 若在 history 模式(搜索跳转过),回 live 起始。historyPrefix(向上加载的历史)保留。
  const onInputChange = useCallback(() => {
    setMatches([]);
    setMatchIdx(-1);
    setHasSearched(false);
    setScrollTargetId(null);
    setHistoryPage(null);
  }, []);

  const nextMatch = useCallback(() => {
    if (!matches.length) return;
    const ni = (matchIdx + 1) % matches.length;
    setMatchIdx(ni);
    jumpToMatch(matches, ni);
  }, [matches, matchIdx, jumpToMatch]);

  const prevMatch = useCallback(() => {
    if (!matches.length) return;
    const ni = (matchIdx - 1 + matches.length) % matches.length;
    setMatchIdx(ni);
    jumpToMatch(matches, ni);
  }, [matches, matchIdx, jumpToMatch]);

  const backToLive = useCallback(() => {
    setHistoryPage(null);
    setHistoryPrefix([]);          // 回最新 = 清顶部历史(纯实时尾)
    setFollowing(true);
    setNewCount(0);
    setAtOldest(false);
  }, []);

  const matchSet = useMemo(() => new Set(matches), [matches]);
  const currentMatch = matchIdx >= 0 ? (matches[matchIdx] ?? null) : null;

  return {
    displayed, mode, newCount, scroller, onScroll,
    matches, matchIdx, searching, hasSearched, matchSet, currentMatch,
    runSearch, onInputChange, nextMatch, prevMatch, backToLive,
    loadingTop, atOldest,
  };
}
