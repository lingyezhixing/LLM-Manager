import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { LogLine, LogSearch } from "@/lib/api";
import { fetchSessionLines, fetchSessions, searchSessionLogs } from "@/lib/api";

const WINDOW = 1500;          // 实时尾窗口 + 历史页大小(行数)
const STICKY_THRESHOLD = 24;  // 贴底判定阈值(px)
const TOP_THRESHOLD = 8;      // 触顶判定阈值(px)——触发向上加载历史
const MAX_PREFIX = 5000;      // historyPrefix 上限(防卡顿;超过丢最旧)

/**
 * 日志数据源抽象:useLogViewer 通过它访问 SSE / 翻页 / 搜索,数据源恒为持久会话
 * (/api/logs/sessions/*):模型页定位该模型最新会话,日志页绑定指定会话。
 * 注意:传给 useLogViewer 的 api 对象必须身份稳定(包装层 useMemo 按 alias/sessionId
 * 缓存),否则 SSE 订阅 effect 会随每次渲染重跑。
 */
export interface LogApi {
  streamUrl: (level?: string) => string;
  fetchPage: (before: number, limit: number, level?: string) => Promise<LogLine[]>;
  search: (q: string, level?: string) => Promise<LogSearch>;
}

export function sessionLogApi(sessionId: number): LogApi {
  return {
    streamUrl: (level) => `/api/logs/sessions/${sessionId}/stream${level ? `?level=${level}` : ""}`,
    fetchPage: (before, limit, level) => fetchSessionLines(sessionId, before, limit, level),
    search: (q, level) => searchSessionLogs(sessionId, q, level),
  };
}

/**
 * 单日志源查看器(单会话持久日志,由 api 参数决定;api=null 时保持空态不订阅)。两种模式:
 *  - live:订阅 streamUrl 的 SSE,实时追加(贴底跟进);滚到顶部自动加载更早历史(prepend 到
 *   historyPrefix),滚轮上滚暂停跟进,来新行累加 newCount。
 *  - history:搜索跳转到窗口外的匹配时载入历史页(fetchPage before=),静态浏览;实时行仍进 liveLines
 *   (后台),用 newCount 记数;「返回最新」切回 live。
 * 搜索:后端全量检索 → 匹配行 id;‹/› 在匹配间跳转,目标不在当前窗口则翻页载入后滚动定位。
 * hasSearched 跟踪「是否真的执行过搜索」(runSearch 调用过),而非「输入框是否有字」——
 * 输入未按 Enter 时不显示「无匹配」,避免误导。
 * level 为后端查询参数(SSE/搜索/翻页/向上加载均带),变更时重订阅 + 清搜索。
 * runKey 为运行实例标识(模型日志传 pid):停止(null)或重启(新进程)时变化 → 重订阅并清空,
 * 使同一 alias 的停止/重启能正确清旧日志、加载新日志(否则 alias/level 不变,旧缓冲残留、
 * EventSource 不重连、重启后新日志进不来,须手动切换模型才重置)。会话日志恒传 null(父级
 * key={sessionId} 重建组件)。
 */
export function useLogViewer(api: LogApi | null, level: string, runKey: number | null) {
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

  // SSE 实时尾(随 api/level/runKey 重订阅,重置视图 + 搜索)。新行不在「实时+跟进」态则记 newCount。
  // runKey(pid)随模型停止/重启变化 → 重连到新进程流并清空旧日志(否则同一 alias 重启后新日志进不来)。
  // 已结束会话:stream 端点对非活跃会话 404 → onerror 且不重连;回退 fetchPage 取最新一页作初始视图
  // (翻页/搜索/向上加载照常)。瞬时错误(运行中会话)EventSource 自行重连;若先收到过行则不回退,
  // 避免与重连后的回填重复。onmessage 里的 id 守卫兜底防重复追加(回退与回填交错时)。
  useEffect(() => {
    if (!api) return;   // 无会话(模型未启动 / 定位中):保持空态
    setLiveLines([]); setHistoryPrefix([]); setHistoryPage(null); setFollowing(true); setNewCount(0);
    setMatches([]); setMatchIdx(-1); setHasSearched(false); setAtOldest(false);
    let receivedAny = false;   // 本次订阅是否收到过行
    const es = new EventSource(api.streamUrl(levelParam));
    es.onmessage = (ev) => {
      try {
        const l = JSON.parse(ev.data) as LogLine;
        receivedAny = true;
        setLiveLines((prev) => {
          if (prev.length > 0 && l.id <= prev[prev.length - 1].id) return prev;  // 防重连回填重复
          const next = prev.length >= WINDOW ? prev.slice(prev.length - WINDOW + 1) : prev;
          return [...next, l];
        });
        if (!(liveRef.current && followingRef.current)) setNewCount((n) => n + 1);
      } catch { /* 帧异常忽略 */ }
    };
    es.onerror = () => {
      if (receivedAny) return;          // 运行中会话的瞬时错误:EventSource 自行重连,不回退
      // 已结束会话:stream 端点 404 → error 且不会重连(规范:非 200 直接 fail)。
      // 运行中会话的首连瞬时错误:回退页先顶上,重连成功后的回填由 id 守卫去重,实时尾照常。
      api.fetchPage(Number.MAX_SAFE_INTEGER, WINDOW, levelParam)
        .then((page) => { setLiveLines((prev) => (prev.length > 0 ? prev : page)); })
        .catch(() => { /* best-effort */ });
    };
    return () => es.close();
  }, [api, levelParam, runKey]);

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
    if (!api || loadingTop || mode !== "live") return;
    const firstId = historyPrefix.length > 0 ? historyPrefix[0].id
      : (liveLines.length > 0 ? liveLines[0].id : null);
    if (firstId == null || firstId <= 1) { setAtOldest(true); return; }   // 已到最早(id 从 1 起)
    const el = scroller.current;
    if (el) pendingTopFixRef.current = { h: el.scrollHeight, t: el.scrollTop }; // prepend 前基准
    setLoadingTop(true);
    try {
      const page = await api.fetchPage(firstId, WINDOW, levelParam);
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
  }, [api, levelParam, loadingTop, mode, historyPrefix, liveLines]);

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
    if (!api || idx < 0 || idx >= all.length) return;
    const target = all[idx];
    const inView = (historyPage ?? liveView).some((l) => l.id === target);
    if (inView) {
      setScrollTargetId(target);
    } else {
      api.fetchPage(target + 1, WINDOW, levelParam)
        .then((page) => { setHistoryPage(page); setScrollTargetId(target); })
        .catch(() => { /* best-effort */ });
    }
  }, [api, levelParam, historyPage, liveView]);

  const runSearch = useCallback(async (q: string) => {
    if (!api) return;
    setHasSearched(true);                    // bug1:标记已执行搜索(无论 q 是否空)
    if (!q.trim()) { setMatches([]); setMatchIdx(-1); return; }
    setSearching(true);
    try {
      const res = await api.search(q, levelParam);
      setMatches(res.matches);
      const idx = res.matches.length ? 0 : -1;
      setMatchIdx(idx);
      if (idx >= 0) jumpToMatch(res.matches, idx);
    } finally { setSearching(false); }
  }, [api, levelParam, jumpToMatch]);

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

/**
 * 单模型日志查看器(模型管理页右栏)。api 按 alias/runKey 定位该模型最新会话:
 * 停止/重启(runKey=pid 变)时重新定位 → 新会话 id → 重订阅。模型从未启动
 * (无会话)→ api=null → 面板空态。签名不变,ModelLogPanel 零改动。
 */
export function useModelLogs(alias: string, level: string, runKey: number | null) {
  const [sessionId, setSessionId] = useState<number | null>(null);
  useEffect(() => {
    let cancelled = false;
    setSessionId(null);
    fetchSessions({ type: "model", model: alias, limit: 1 })
      .then((s) => { if (!cancelled && s.length > 0) setSessionId(s[0].id); })
      .catch(() => { /* 后端不可达:保持空态 */ });
    return () => { cancelled = true; };
  }, [alias, runKey]);
  const api = useMemo(
    () => (sessionId == null ? null : sessionLogApi(sessionId)),
    [sessionId],
  );
  return useLogViewer(api, level, runKey);
}

/** 单会话日志(日志查看页)。runKey 恒 null——切会话由父级 key={sessionId} 重建组件。 */
export function useSessionLogs(sessionId: number, level: string) {
  const api = useMemo(() => sessionLogApi(sessionId), [sessionId]);
  return useLogViewer(api, level, null);
}
