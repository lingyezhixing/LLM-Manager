import { useEffect, useState } from "react";

/**
 * 订阅 SSE 端点。返回 { data, error }:data = 最新事件负载
 * (解析后的 JSON),首个事件前为 null;error 在连接
 * 失败期间为 true(服务宕机 / 流断开),下次成功(重)连时清除。
 * EventSource 断流自动重连,无需手动重试。卸载(或 url 变更)时
 * 关闭流——正是关闭动作让后端按订阅数计数的循环停止
 * (例如设备刷新循环随 bar 卸载而停止)。
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
        /* 帧格式异常 — 忽略;下一次刷新会替换 */
      }
    };
    es.onerror = () => setError(true);   // 瞬断/宕机 → 置错;重连成功 onopen 清错
    return () => es.close();
  }, [url]);

  return { data, error };
}
