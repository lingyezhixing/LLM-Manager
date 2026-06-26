import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { fetchModels, type ModelInfo } from "@/lib/api";

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="flex-1 rounded-lg border border-border px-3 py-2">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="text-lg font-semibold">{value}</div>
    </div>
  );
}

/** Model status summary (概览). Refetches /api/models every 3s. */
export function ModelSummary() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["models"],
    queryFn: fetchModels,
    refetchInterval: 3000,
  });
  if (isLoading) return <p className="text-sm text-muted-foreground">加载中…</p>;
  if (error) return <p className="text-sm text-destructive">加载失败</p>;

  const models: ModelInfo[] = data?.data ?? [];
  const running = models.filter((m) => m.status === "routing");
  const stopped = models.filter((m) => m.status === "stopped");
  const failed = models.filter((m) => m.status === "failed");

  return (
    <div>
      <div className="mb-3 flex gap-3">
        <Stat label="运行中" value={running.length} />
        <Stat label="已停止" value={stopped.length} />
        <Stat label="失败" value={failed.length} />
      </div>
      <div className="mb-2 flex items-center justify-between">
        <span className="text-xs uppercase text-muted-foreground">运行中的模型</span>
        <Link to="/models" className="text-xs text-primary hover:underline">管理全部 →</Link>
      </div>
      {running.length === 0 ? (
        <p className="text-sm text-muted-foreground">无运行中的模型</p>
      ) : (
        <div className="flex flex-col gap-2">
          {running.map((m) => (
            <div
              key={m.alias}
              className="flex items-center justify-between rounded-md border border-border px-3 py-2 text-sm"
            >
              <span className="truncate">{m.alias}</span>
              <span className="text-muted-foreground">
                :{m.port}
                {m.pending > 0
                  ? ` · ${m.pending} 请求中`
                  : m.idle_seconds != null
                    ? ` · 空闲 ${Math.round(m.idle_seconds)}s`
                    : ""}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
