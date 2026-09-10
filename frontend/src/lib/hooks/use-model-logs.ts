import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, useReducer } from "react";
import type { LogLine, LogSearch } from "@/lib/api";
import { fetchSessionLines, fetchSessions, LOG_PAGE_LIMIT, searchSessionLogs } from "@/lib/api";

const WINDOW = LOG_PAGE_LIMIT; // 实时尾窗口 + 历史页大小(与 fetchSessionLines 默认 limit 同源)
const STICKY_THRESHOLD = 24;  // 贴底判定阈值(px)
const TOP_THRESHOLD = 8;      // 触顶判定阈值(px)——触发向上加载历史
const MAX_PREFIX = 5000;      // historyPrefix 上限(防卡顿;超过丢最旧)

/**
 * 日志数据源抽象:useLogViewer 通过它访问 SSE / 翻页 / 搜索,数据源恒为持久会话
 * (/api/logs/sessions/*):模型页定位该模型最新会话,日志页绑定指定会话。
 * 注意:传给 useLogViewer 的 api 对象必须身份稳定(包装层 useMemo 按 alias/sessionId
 * 缓存),否则 SSE 订阅 effect 会随每次渲染重跑。
 */
interface LogApi {
  streamUrl: (level?: string) => string;
  fetchPage: (before: number, limit: number, level?: string) => Promise<LogLine[]>;
  search: (q: string, level?: string) => Promise<LogSearch>;
  /** 已知会话已结束(日志页列表 status=ended):跳过 SSE(避免对历史会话发
   *  stream 请求的 404 噪音),直接在订阅初始化时回退页加载。 */
  ended?: boolean;
}

function sessionLogApi(sessionId: number, ended = false): LogApi {
  return {
    streamUrl: (level) => `/api/logs/sessions/${sessionId}/stream${level ? `?level=${level}` : ""}`,
    fetchPage: (before, limit, level) => fetchSessionLines(sessionId, before, limit, level),
    search: (q, level) => searchSessionLogs(sessionId, q, level),
    ended,
  };
}

/**
 * Reducer 状态:视图态(表单态 level/input 留 useState,生命周期不同)。
 */
export interface ViewerState {
  liveLines: LogLine[];
  historyPrefix: LogLine[];
  historyPage: LogLine[] | null;
  following: boolean;
  newCount: number;
  matches: number[];
  matchTotal: number;
  matchIdx: number;
  hasSearched: boolean;
  searching: boolean;
  scrollTargetId: number | null;
  atOldest: boolean;
}

const initialState: ViewerState = {
  liveLines: [],
  historyPrefix: [],
  historyPage: null,
  following: true,
  newCount: 0,
  matches: [],
  matchTotal: 0,
  matchIdx: -1,
  hasSearched: false,
  searching: false,
  scrollTargetId: null,
  atOldest: false,
};

type Action =
  | { t: "reset" }                       // api/level/runKey 重订阅(代次由 genRef 管,不进 state)
  | { t: "sse"; line: LogLine }          // 实时行(WINDOW 裁剪 + id 守卫防重连重复)
  | { t: "fallback"; page: LogLine[] }   // 已结束会话回退页(仅当前为空时顶上)
  | { t: "following"; v: boolean }
  | { t: "increment-new" }
  | { t: "clear-new" }
  | { t: "prepend"; lines: LogLine[] }   // 向上加载(MAX_PREFIX 裁剪)
  | { t: "history-page"; page: LogLine[] }
  | { t: "back-live" }
  | { t: "search-start" }
  | { t: "search-done"; matches: number[]; total: number }
  | { t: "input" }                       // 清搜索回未搜态
  | { t: "match-idx"; i: number }
  | { t: "scroll-target"; id: number | null }
  | { t: "at-oldest"; v: boolean }
  | { t: "searching"; v: boolean };

/**
 * Reducer:视图态状态机。
 */
export function viewerReducer(state: ViewerState, action: Action): ViewerState {
  switch (action.t) {
    case "reset":
      return { ...initialState };

    case "sse": {
      // 防重连回填重复(id 守卫) + WINDOW 裁剪
      const { line } = action;
      const prev = state.liveLines;
      if (prev.length > 0 && line.id <= prev[prev.length - 1].id) return state;
      const next = prev.length >= WINDOW ? prev.slice(prev.length - WINDOW + 1) : prev;
      return { ...state, liveLines: [...next, line] };
    }

    case "fallback":
      // 仅当前为空时顶上
      return state.liveLines.length === 0 ? { ...state, liveLines: action.page } : state;

    case "following":
      // 相等短路:onScroll 每 tick 都 dispatch(值常重复),不改状态引用则无重渲染
      return state.following === action.v ? state : { ...state, following: action.v };

    case "increment-new":
      return { ...state, newCount: state.newCount + 1 };

    case "clear-new":
      return state.newCount === 0 ? state : { ...state, newCount: 0 };

    case "prepend": {
      // MAX_PREFIX 裁剪 + 去重由调用方过滤(newer)
      const merged = [...action.lines, ...state.historyPrefix];
      const next = merged.length > MAX_PREFIX ? merged.slice(merged.length - MAX_PREFIX) : merged;
      return { ...state, historyPrefix: next };
    }

    case "history-page":
      return { ...state, historyPage: action.page };

    case "back-live":
      // 清历史页 + historyPrefix + 重置 following/newCount/atOldest
      return {
        ...state,
        historyPage: null,
        historyPrefix: [],
        following: true,
        newCount: 0,
        atOldest: false,
      };

    case "search-start":
      // 标记已执行搜索 + 清空匹配
      return {
        ...state,
        hasSearched: true,
        matches: [],
        matchTotal: 0,
        matchIdx: -1,
      };

    case "search-done":
      // 设置匹配 + matchTotal + 初始索引
      return {
        ...state,
        matches: action.matches,
        matchTotal: action.total,
        matchIdx: action.matches.length ? 0 : -1,
      };

    case "input":
      // 清搜索 + 回未搜态 + 清历史页 + scrollTarget;输入变更时在途搜索作废
      return {
        ...state,
        matches: [],
        matchTotal: 0,
        matchIdx: -1,
        hasSearched: false,
        searching: false,
        scrollTargetId: null,
        historyPage: null,
      };

    case "match-idx":
      return { ...state, matchIdx: action.i };

    case "scroll-target":
      return { ...state, scrollTargetId: action.id };

    case "at-oldest":
      return { ...state, atOldest: action.v };

    case "searching":
      // 相等短路(onInput 会重复 dispatch false)
      return state.searching === action.v ? state : { ...state, searching: action.v };

    default:
      return state;
  }
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
 * level/input 归 hook 持有,经共享 LogLines 读写:level 为后端查询参数(SSE/搜索/
 * 翻页/向上加载均带),变更时重订阅 + 清搜索;input 为搜索输入框文本。
 * runKey 为运行实例标识(模型日志传 pid):停止(null)或重启(新进程)时变化 → 重订阅并清空,
 * 使同一 alias 的停止/重启能正确清旧日志、加载新日志(否则 alias/level 不变,旧缓冲残留、
 * EventSource 不重连、重启后新日志进不来,须手动切换模型才重置)。会话日志恒传 null(父级
 * key={sessionId} 重建组件)。
 */
export function useLogViewer(api: LogApi | null, runKey: number | null) {
  const [state, dispatch] = useReducer(viewerReducer, initialState);
  const [level, setLevel] = useState<string>("");   // 级别过滤(后端查询参数);变更 → 重订阅 + 清搜索
  const [input, setInput] = useState("");           // 搜索输入框文本(经 LogLines 读写)
  const levelParam = level || undefined;

  const scroller = useRef<HTMLDivElement | null>(null);
  const pendingTopFixRef = useRef<{ h: number; t: number } | null>(null); // prepend 视口维持基准
  const loadingTopRef = useRef(false);   // 向上加载重入守卫(同步 ref,防触顶连续滚动重入)
  const followingRef = useRef(true);
  const liveRef = useRef(true);
  const genRef = useRef(0);  // 视图代次——api/runKey 重订阅时 ++,在途异步(回退/翻页/搜索)先比对再 dispatch
  const searchGenRef = useRef(0);  // 私有搜索代次——与 SSE 代次分离,专管搜索四路失效面(搜索对搜索/输入变更/空查询/finally 熄灯)
  useEffect(() => { followingRef.current = state.following; }, [state.following]);
  useEffect(() => { liveRef.current = state.historyPage === null; }, [state.historyPage]);

  const liveView = useMemo(() => [...state.historyPrefix, ...state.liveLines], [state.historyPrefix, state.liveLines]);
  const displayed = state.historyPage ?? liveView;
  const mode: "live" | "history" = state.historyPage ? "history" : "live";

  // SSE 实时尾(随 api/level/runKey 重订阅,重置视图 + 搜索)。新行不在「实时+跟进」态则记 newCount。
  // runKey(pid)随模型停止/重启变化 → 重连到新进程流并清空旧日志(否则同一 alias 重启后新日志进不来)。
  // 已结束会话:stream 端点对非活跃会话 404 → onerror 且不重连;回退 fetchPage 取最新一页作初始视图
  // (翻页/搜索/向上加载照常)。瞬时错误(运行中会话)EventSource 自行重连;若先收到过行则不回退,
  // 避免与重连后的回填重复。onmessage 里的 id 守卫兜底防重复追加(回退与回填交错时)。
  useEffect(() => {
    dispatch({ t: "reset" });
    if (!api) return;   // 无会话(模型未启动 / 定位中):视图已重置,保持空态
    const gen = ++genRef.current;   // 代次递增——旧代次在途回调发现代次不符即丢弃
    let receivedAny = false;   // 本次订阅是否收到过行
    // 已结束会话:跳过 SSE,回退页顶替(消除流端点 404 噪音)。onerror 回退逻辑复用
    // fetchPage(MAX, WINDOW) 路径——两者同形,gen 守卫一致防代次错位。
    const fallback = () => {
      api.fetchPage(Number.MAX_SAFE_INTEGER, WINDOW, levelParam)
        .then((page) => {
          if (genRef.current !== gen) return;  // 代次已变(重订阅),丢弃旧回退页
          dispatch({ t: "fallback", page });
        })
        .catch(() => { /* 尽力而为 */ });
    };
    if (api.ended) {
      fallback();
      return () => undefined;   // 无 EventSource 需关闭;代次守卫由下一次订阅的 ++ 完成
    }
    const es = new EventSource(api.streamUrl(levelParam));
    es.onmessage = (ev) => {
      try {
        const l = JSON.parse(ev.data) as LogLine;
        receivedAny = true;
        dispatch({ t: "sse", line: l });
        if (!(liveRef.current && followingRef.current)) dispatch({ t: "increment-new" });
      } catch { /* 帧异常忽略 */ }
    };
    es.onerror = () => {
      if (receivedAny) return;          // 运行中会话的瞬时错误:EventSource 自行重连,不回退
      // 已结束会话:stream 端点 404 → error 且不会重连(规范:非 200 直接 fail)。
      // 运行中会话的首连瞬时错误:回退页先顶上,重连成功后的回填由 id 守卫去重,实时尾照常。
      fallback();
    };
    return () => es.close();
  }, [api, levelParam, runKey]);

  // 跟进:live + following → 新行贴底。
  useEffect(() => {
    if (mode === "live" && state.following && scroller.current) {
      scroller.current.scrollTop = scroller.current.scrollHeight;
    }
  }, [state.liveLines, state.following, mode]);

  // prepend 后维持视口位置(DOM 更新后同步调整 scrollTop,在 paint 前)。
  useLayoutEffect(() => {
    const fix = pendingTopFixRef.current;
    const el = scroller.current;
    if (fix && el) {
      el.scrollTop = fix.t + (el.scrollHeight - fix.h);
      pendingTopFixRef.current = null;
    }
  }, [state.historyPrefix]);

  // 滚动到搜索目标(渲染后)。
  useEffect(() => {
    if (state.scrollTargetId == null || !scroller.current) return;
    const el = scroller.current.querySelector(`[data-line-id="${state.scrollTargetId}"]`);
    if (el) (el as HTMLElement).scrollIntoView({ block: "center" });
    dispatch({ t: "scroll-target", id: null });
  }, [displayed, state.scrollTargetId]);

  // 向上加载更早日志——prepend 到 historyPrefix,维持视口,防抖,上限,到顶。
  // 重入守卫用同步 ref(置位即生效)——触顶连续滚动若用 state 判定,更新生效前会
  // 用旧闭包重入,导致同页历史重复 prepend + key 冲突。
  const loadMoreAbove = useCallback(async () => {
    if (!api || loadingTopRef.current || mode !== "live") return;
    const firstId = state.historyPrefix.length > 0 ? state.historyPrefix[0].id
      : (state.liveLines.length > 0 ? state.liveLines[0].id : null);
    if (firstId == null || firstId <= 1) { dispatch({ t: "at-oldest", v: true }); return; }   // 已到最早(id 从 1 起)
    const el = scroller.current;
    if (el) pendingTopFixRef.current = { h: el.scrollHeight, t: el.scrollTop }; // prepend 前基准
    loadingTopRef.current = true;                 // 同步置位 ref 守卫
    const gen = genRef.current;                   // 比对代次,防重订阅后旧页混入新视图
    try {
      const page = await api.fetchPage(firstId, WINDOW, levelParam);
      if (genRef.current !== gen) return;         // 代次已变,丢弃
      const newer = page.filter((l) => l.id < firstId);   // 去重(后端返回 id<firstId 的最近 WINDOW 行)
      if (newer.length === 0) { dispatch({ t: "at-oldest", v: true }); return; }
      dispatch({ t: "prepend", lines: newer });
      if (newer[0].id <= 1) dispatch({ t: "at-oldest", v: true });             // 加载到最早(id=1)
    } catch { /* 尽力而为 */ } finally {
      loadingTopRef.current = false;
    }
  }, [api, levelParam, mode, state.historyPrefix, state.liveLines]);

  const onScroll = useCallback(() => {
    if (mode === "history") return;          // 历史页不自动跟进/加载
    const el = scroller.current;
    if (!el) return;
    if (el.scrollTop <= TOP_THRESHOLD) loadMoreAbove();    // 触顶 → 加载更早
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < STICKY_THRESHOLD;
    dispatch({ t: "following", v: atBottom });
    if (atBottom) dispatch({ t: "clear-new" });
  }, [mode, loadMoreAbove]);

  // 跳到 all[idx]:在当前窗口则滚动定位,否则翻页载入(target 为页底)再定位。
  // 在途翻页以双代次校验(视图代次 + 搜索代次)——输入变更/新搜索后旧跳转不落回。
  const jumpToMatch = useCallback((all: number[], idx: number) => {
    if (!api || idx < 0 || idx >= all.length) return;
    const target = all[idx];
    const inView = (state.historyPage ?? liveView).some((l) => l.id === target);
    if (inView) {
      dispatch({ t: "scroll-target", id: target });
    } else {
      const gen = genRef.current;              // 重订阅后旧翻页不落新视图
      const sgen = searchGenRef.current;       // 搜索代次快照(输入变更/新搜索作废)
      api.fetchPage(target + 1, WINDOW, levelParam)
        .then((page) => {
          if (genRef.current !== gen || searchGenRef.current !== sgen) return;
          dispatch({ t: "history-page", page });
          dispatch({ t: "scroll-target", id: target });
        })
        .catch(() => { /* 尽力而为 */ });
    }
  }, [api, levelParam, state.historyPage, liveView]);

  const runSearch = useCallback(async (q: string) => {
    if (!api) return;
    dispatch({ t: "search-start" });                    // 标记已执行搜索(无论 q 是否空)
    if (!q.trim()) {
      searchGenRef.current++;  // 空查询:作废一切在途搜索结果
      dispatch({ t: "searching", v: false });
      return;
    }
    const gen = ++searchGenRef.current;  // 新代次:旧在途搜索(搜索对搜索/输入变更)比对后丢弃
    dispatch({ t: "searching", v: true });
    try {
      const res = await api.search(q, levelParam);
      if (searchGenRef.current !== gen) return;
      dispatch({ t: "search-done", matches: res.matches, total: res.total });
      const idx = res.matches.length ? 0 : -1;
      if (idx >= 0) jumpToMatch(res.matches, idx);
    } finally {
      if (searchGenRef.current === gen) dispatch({ t: "searching", v: false });  // 非陈旧 finally 才熄灯
    }
  }, [api, levelParam, jumpToMatch]);

  // 用户改输入 → 更新输入框文本 + 清上次搜索结果 + hasSearched(回「未搜索」态,不显示「无匹配」)。
  // 若在 history 模式(搜索跳转过),回 live 起始。historyPrefix(向上加载的历史)保留。
  const onInput = useCallback((v: string) => {
    setInput(v);
    searchGenRef.current++;  // 输入变更:作废在途搜索(旧响应不得落回)
    dispatch({ t: "input" });
  }, []);

  const nextMatch = useCallback(() => {
    if (!state.matches.length) return;
    const ni = (state.matchIdx + 1) % state.matches.length;
    dispatch({ t: "match-idx", i: ni });
    jumpToMatch(state.matches, ni);
  }, [state.matches, state.matchIdx, jumpToMatch]);

  const prevMatch = useCallback(() => {
    if (!state.matches.length) return;
    const ni = (state.matchIdx - 1 + state.matches.length) % state.matches.length;
    dispatch({ t: "match-idx", i: ni });
    jumpToMatch(state.matches, ni);
  }, [state.matches, state.matchIdx, jumpToMatch]);

  const backToLive = useCallback(() => {
    dispatch({ t: "back-live" });
  }, []);

  const matchSet = useMemo(() => new Set(state.matches), [state.matches]);
  const currentMatch = state.matchIdx >= 0 ? (state.matches[state.matchIdx] ?? null) : null;

  return {
    displayed, mode, newCount: state.newCount, scroller, onScroll,
    matches: state.matches, matchTotal: state.matchTotal, matchIdx: state.matchIdx,
    searching: state.searching, hasSearched: state.hasSearched, matchSet, currentMatch,
    runSearch, onInput, nextMatch, prevMatch, backToLive,
    atOldest: state.atOldest,
    level, setLevel, input,
  };
}

/**
 * 单模型日志查看器(模型管理页右栏)。api 按 alias/runKey 定位该模型最新会话:
 * 停止/重启(runKey=pid 变)时重新定位 → 新会话 id → 重订阅。模型从未启动
 * (无会话)→ api=null → 面板空态。enabled=false(模型未运行)→ 不定位历史会话,
 * 保持空态(避免停止后仍展示上一次的日志)。level/input 由 useLogViewer 持有,
 * ModelLogPanel 经共享 LogLines 读写。
 */
export function useModelLogs(alias: string, runKey: number | null, enabled = true) {
  const [sessionId, setSessionId] = useState<number | null>(null);
  useEffect(() => {
    if (!enabled) { setSessionId(null); return; }   // 模型未运行:不定位历史会话,保持空态
    let cancelled = false;
    setSessionId(null);
    fetchSessions({ type: "model", model: alias, limit: 1 })
      .then((s) => { if (!cancelled && s.length > 0) setSessionId(s[0].id); })
      .catch(() => { /* 后端不可达:保持空态 */ });
    return () => { cancelled = true; };
  }, [alias, runKey, enabled]);
  const api = useMemo(
    () => (sessionId == null ? null : sessionLogApi(sessionId)),
    [sessionId],
  );
  return useLogViewer(api, runKey);
}

/** 单会话日志(日志查看页)。runKey 恒 null——切会话由父级 key={sessionId} 重建组件。 */
export function useSessionLogs(sessionId: number, ended = false) {
  const api = useMemo(() => sessionLogApi(sessionId, ended), [sessionId, ended]);
  return useLogViewer(api, null);
}
