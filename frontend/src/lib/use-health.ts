import { useQuery } from "@tanstack/react-query";
import { fetchHealth } from "@/lib/api";

/**
 * Backend reachable? Polls /health every 5s. Optimistic online until a fetch errors
 * (so the LED doesn't flash on first load), flips to offline once a probe fails and
 * back when it recovers. Drives the pill-bar health LED (▣ online / □ offline).
 */
export function useHealth(): boolean {
  const q = useQuery({
    queryKey: ["health"],
    queryFn: fetchHealth,
    refetchInterval: 5000,
    retry: 1,
  });
  return q.status !== "error";
}
