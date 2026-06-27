import { useEffect, useRef, useState } from "react";
import type { LogLine } from "@/lib/api";

const DOM_CAP = 1500;        // 前端只留最近 N 行(Phase 2 虚拟化);旧的在后端
const STICKY_THRESHOLD = 24;

/**
 * 订阅 /api/models/{alias}/logs/stream。返回 {lines, following, newCount, scroller, onScroll, jumpBottom}。
 * 隐式跟进:贴底→自动跟;滚轮离开底部→停;回底→恢复。离底期间来新行累加 newCount。
 */
export function useModelLogs(alias: string) {
  const [lines, setLines] = useState<LogLine[]>([]);
  const [following, setFollowing] = useState(true);
  const [newCount, setNewCount] = useState(0);
  const scroller = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    setLines([]); setFollowing(true); setNewCount(0);
    const es = new EventSource(`/api/models/${encodeURIComponent(alias)}/logs/stream`);
    es.onmessage = (ev) => {
      try {
        const l = JSON.parse(ev.data) as LogLine;
        setLines((prev) => {
          const next = prev.length >= DOM_CAP ? prev.slice(prev.length - DOM_CAP + 1) : prev;
          return [...next, l];
        });
        setFollowing((f) => { if (!f) setNewCount((n) => n + 1); return f; });
      } catch { /* 帧异常忽略 */ }
    };
    return () => es.close();
  }, [alias]);

  useEffect(() => {
    if (following && scroller.current) scroller.current.scrollTop = scroller.current.scrollHeight;
  }, [lines, following]);

  const onScroll = () => {
    const el = scroller.current; if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < STICKY_THRESHOLD;
    setFollowing(atBottom);
    if (atBottom) setNewCount(0);
  };
  const jumpBottom = () => {
    if (scroller.current) scroller.current.scrollTop = scroller.current.scrollHeight;
    setFollowing(true); setNewCount(0);
  };

  return { lines, following, newCount, scroller, onScroll, jumpBottom };
}
