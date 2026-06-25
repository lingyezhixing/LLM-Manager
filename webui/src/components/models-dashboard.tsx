import { useQuery } from "@tanstack/react-query";
import { fetchModels, type ModelInfo } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

const STATUS: Record<string, { label: string; cls: string }> = {
  routing: { label: "运行中", cls: "bg-success text-success-foreground" },
  starting: { label: "启动中", cls: "bg-primary text-primary-foreground" },
  init_script: { label: "初始化", cls: "bg-primary text-primary-foreground" },
  health_check: { label: "健康检查", cls: "bg-primary text-primary-foreground" },
  failed: { label: "失败", cls: "bg-destructive text-destructive-foreground" },
  stopped: { label: "已停止", cls: "bg-muted text-muted-foreground" },
};

function ModelCard({ m }: { m: ModelInfo }) {
  const s = STATUS[m.status] ?? { label: m.status, cls: "bg-muted text-muted-foreground" };
  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <CardTitle>{m.alias}</CardTitle>
          <Badge className={s.cls}>{s.label}</Badge>
        </div>
      </CardHeader>
      <CardContent>
        <div className="text-sm text-muted-foreground">
          PID {m.pid ?? "—"} · 端口 {m.port} · {m.mode} 模式
          {m.idle_seconds != null ? ` · 空闲 ${Math.round(m.idle_seconds)}s` : ""}
          {m.failure_reason ? ` · ${m.failure_reason}` : ""}
          {m.pending > 0 ? ` · ${m.pending} 请求中` : ""}
        </div>
      </CardContent>
    </Card>
  );
}

export function ModelsDashboard() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["models"],
    queryFn: fetchModels,
    refetchInterval: 3000,
  });
  if (isLoading) return <p className="text-muted-foreground">加载中…</p>;
  if (error) return <p className="text-destructive">加载失败:{String(error)}</p>;
  const models = data?.data ?? [];
  if (models.length === 0) return <p className="text-muted-foreground">暂无模型</p>;
  return (
    <div className="grid gap-3">
      {models.map((m) => <ModelCard key={m.alias} m={m} />)}
    </div>
  );
}
