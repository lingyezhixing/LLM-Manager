import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { LogLine } from "@/lib/api";
import { fetchLogPage, searchLogs } from "@/lib/api";

const WINDOW = 1500;          // 实时尾窗口 + 历史页大小(行数)
const STICKY_THRESHOLD = 24;  // 贴底判定阈值(px)

/**
 * 单模型日志查看器。两种模式:
 *  - live:订阅 /logs/stream,实时追加(贴底跟进);滚轮上滚暂停跟进,来新行累加 newCount。
 *  - history:搜索跳转到窗口外的匹配时载入历史页(/logs?before=),静态浏览;实时行仍进 liveLines
 *   (后台),用 newCount 记数;「返回最新」切回 live。
 * 搜索:后端全量检索 → 匹配行 id;‹/› 在匹配间跳转,目标不在当前窗口则翻页载入后滚动定位。
 * level 为后端查询参数(SSE/搜索/翻页均带),变更时重订阅 + 清搜索。
 */
export function useModelLogs(alias: string, level: string) {
  const levelParam = level || undefined;
  const [liveLines, setLiveLines] = useState<LogLine[]>([]);
  const [historyPage, setHistoryPage] = useState<LogLine[] | null>(null);
  const [following, setFollowing] = useState(true);
  const [newCount, setNewCount] = useState(0);
  const [matches, setMatches] = useState<number[]>([]);
  const [matchIdx, setMatchIdx] = useState(-1);
  const [searching, setSearching] = useState(false);
  const [scrollTargetId, setScrollTargetId] = useState<number | null>(null);

  const scroller = useRef<HTMLDivElement | null>(null);
  const followingRef = useRef(true);
  const liveRef = useRef(true);
  useEffect(() => { followingRef.current = following; }, [following]);
  useEffect(() => { liveRef.current = historyPage === null; }, [historyPage]);

  const displayed = historyPage ?? liveLines;
  const mode: "live" | "history" = historyPage ? "history" : "live";

  // SSE 实时尾(随 alias/level 重订阅,重置视图 + 搜索)。新行不在「实时+跟进」态则记 newCount。
  useEffect(() => {
    setLiveLines([]); setHistoryPage(null); setFollowing(true); setNewCount(0);
    setMatches([]); setMatchIdx(-1);
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

  // 滚动到搜索目标(渲染后)。
  useEffect(() => {
    if (scrollTargetId == null || !scroller.current) return;
    const el = scroller.current.querySelector(`[data-line-id="${scrollTargetId}"]`);
    if (el) (el as HTMLElement).scrollIntoView({ block: "center" });
    setScrollTargetId(null);
  }, [displayed, scrollTargetId]);

  const onScroll = useCallback(() => {
    if (mode === "history") return;          // 历史页不自动跟进
    const el = scroller.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < STICKY_THRESHOLD;
    setFollowing(atBottom);
    if (atBottom) setNewCount(0);
  }, [mode]);

  // 跳到 all[idx]:在当前窗口则滚动定位,否则翻页载入(target 为页底)再定位。
  const jumpToMatch = useCallback((all: number[], idx: number) => {
    if (idx < 0 || idx >= all.length) return;
    const target = all[idx];
    const inView = (historyPage ?? liveLines).some((l) => l.id === target);
    if (inView) {
      setScrollTargetId(target);
    } else {
      fetchLogPage(alias, target + 1, WINDOW, levelParam)
        .then((page) => { setHistoryPage(page); setScrollTargetId(target); })
        .catch(() => { /* best-effort */ });
    }
  }, [alias, levelParam, historyPage, liveLines]);

  const runSearch = useCallback(async (q: string) => {
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
    setFollowing(true);
    setNewCount(0);              // follow 效果会在 mode→live 时贴底
  }, []);

  const matchSet = useMemo(() => new Set(matches), [matches]);
  const currentMatch = matchIdx >= 0 ? (matches[matchIdx] ?? null) : null;

  return {
    displayed, mode, newCount, scroller, onScroll,
    matches, matchIdx, searching, matchSet, currentMatch,
    runSearch, nextMatch, prevMatch, backToLive,
  };
}
