import { useEffect, useState } from "react";

/**
 * Subscribe to an SSE endpoint; returns the latest event payload (parsed JSON), or null
 * before the first event. EventSource auto-reconnects on drop. The stream closes on
 * unmount (or url change) — which is what gates the backend subscriber-counted loops
 * (e.g. the device refresh loop stops when the bar unmounts).
 */
export function useEventStream<T>(url: string): T | null {
  const [data, setData] = useState<T | null>(null);

  useEffect(() => {
    const es = new EventSource(url);
    es.onmessage = (ev) => {
      try {
        setData(JSON.parse(ev.data) as T);
      } catch {
        /* malformed frame — ignore; the next refresh replaces it */
      }
    };
    return () => es.close();
  }, [url]);

  return data;
}
