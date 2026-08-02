import { useQuery } from "@tanstack/react-query";
import { fetchSystemInfo } from "@/lib/api";

// 数据库页:仅展示数据库大小。载入(挂载)时获取一次,不轮询。
// refetchOnMount: "always" — 每次切到该页必重新获取(refetchOnMount: true 只在 stale 时,数据可能未 stale)。
function formatBytes(n: number | null): string {
  if (n == null) return "—";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)} MB`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)} KB`;
  return `${n} B`;
}

export function DatabasePanel() {
  const { data, isLoading } = useQuery({
    queryKey: ["system", "info"],
    queryFn: fetchSystemInfo,
    refetchOnMount: "always",
  });
  if (isLoading || !data) {
    return <div className="text-sm text-muted-foreground">加载中…</div>;
  }
  return (
    <div className="max-w-xs rounded-lg border border-border px-3 py-2">
      <div className="text-xs text-muted-foreground">数据库大小</div>
      <div className="mt-0.5 break-all text-base font-semibold text-foreground">{formatBytes(data.db_size_bytes)}</div>
    </div>
  );
}
