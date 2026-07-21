import { useSystemInfo } from "@/lib/use-config";
import { useNowTick } from "@/lib/use-now";

function formatBytes(n: number | null): string {
  if (n == null) return "—";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)} MB`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)} KB`;
  return `${n} B`;
}

function formatDuration(sec: number): string {
  const s = Math.max(0, Math.floor(sec));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const ss = s % 60;
  const pad = (x: number) => String(x).padStart(2, "0");
  return `${pad(h)}:${pad(m)}:${pad(ss)}`;
}

function Tile({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-border px-3 py-2">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="mt-0.5 break-all text-base font-semibold text-foreground">{value}</div>
    </div>
  );
}

export function SystemInfoPanel() {
  const { data, isLoading } = useSystemInfo();
  const now = useNowTick();
  if (isLoading || !data) {
    return <div className="text-sm text-muted-foreground">加载中…</div>;
  }
  const uptime = now / 1000 - data.started_at;
  return (
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
      <Tile label="版本" value={data.version} />
      <Tile label="运行时长" value={formatDuration(uptime)} />
      <Tile label="数据库大小" value={formatBytes(data.db_size_bytes)} />
      <Tile label="数据库路径" value={data.db_path} />
      <Tile label="日志目录" value={data.log_dir} />
      <Tile label="启动时间" value={new Date(data.started_at * 1000).toLocaleString()} />
    </div>
  );
}
