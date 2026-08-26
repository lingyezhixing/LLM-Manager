import { useQuery } from "@tanstack/react-query";
import { fetchHealth } from "@/lib/api";
import { qk } from "@/lib/api/keys";

/**
 * 后端可达?每 5s 轮询 /health。首次加载乐观显示在线,直到某次 fetch 报错
 * (LED 首载不闪),探测失败即翻离线,恢复后翻回在线。
 * 驱动 pill-bar 健康 LED(▣ 在线 / □ 离线)。
 */
export function useHealth(): boolean {
  const q = useQuery({
    queryKey: qk.health,
    queryFn: fetchHealth,
    refetchInterval: 5000,
    retry: 1,
  });
  return q.status !== "error";
}
