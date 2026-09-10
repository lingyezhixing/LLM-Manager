import { useEffect, useState } from "react";

/**
 * 每 `intervalMs` 刷新 `now`(epoch 毫秒),使时间派生显示(uptime、idle)在本地
 * 更新而无需 refetch — 后端一次性下发墙钟时间戳,客户端
 * 每个 tick 计算流逝值。
 */
export function useNowTick(intervalMs = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
  return now;
}
