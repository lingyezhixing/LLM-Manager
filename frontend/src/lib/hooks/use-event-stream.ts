import { useEffect, useState } from "react";

/**
 * Subscribe to an SSE endpoint. Returns { data, error }: data = latest event payload
 * (parsed JSON) or null before the first event; error = true while the connection is
 * failing (server down / stream dropped), cleared on next successful (re)connect.
 * EventSource auto-reconnects on drop, so no manual retry is needed. The stream closes
 * on unmount (or url change) — which is what gates the backend subscriber-counted loops
 * (e.g. the device refresh loop stops when the bar unmounts).
 */
export function useEventStream<T>(url: string): { data: T | null; error: boolean } {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    setError(false);   // url 变更 → 重订阅,清错误(与清 data 一致:新源未回即显示加载中)
    const es = new EventSource(url);
    es.onopen = () => setError(false);
    es.onmessage = (ev) => {
      try {
        setData(JSON.parse(ev.data) as T);
      } catch {
        /* malformed frame — ignore; the next refresh replaces it */
      }
    };
    es.onerror = () => setError(true);   // 瞬断/宕机 → 置错;重连成功 onopen 清错
    return () => es.close();
  }, [url]);

  return { data, error };
}
