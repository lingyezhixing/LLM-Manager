import { useEventStream } from "@/lib/use-event-stream";
import type { DevicesResponse } from "@/lib/api";

/** Real-time device bar (概览 top). Subscribes to /api/devices/stream (2s push). */
function mem(mb: number): string {
  return mb >= 1024 ? `${(mb / 1024).toFixed(1)}G` : `${mb}M`;
}

function DeviceCard({ d }: { d: DevicesResponse["data"][number] }) {
  const isCpu = d.device_type === "CPU";
  return (
    <div className="min-w-[150px] flex-1 rounded-lg border border-border bg-card p-3">
      <div className="flex items-center justify-between text-sm font-medium">
        <span className="truncate">{d.device_name}</span>
        <span className="text-xs text-muted-foreground">{isCpu ? "CPU" : "GPU"}</span>
      </div>
      <div className="mt-2 flex items-center justify-between text-xs text-muted-foreground">
        <span>利用率</span>
        <span>{Math.round(d.usage_percentage)}%</span>
      </div>
      <div className="mt-1 h-1 overflow-hidden rounded bg-muted">
        <div
          className="h-full bg-primary transition-[width] duration-300"
          style={{ width: `${Math.min(100, Math.max(0, d.usage_percentage))}%` }}
        />
      </div>
      <div className="mt-2 flex items-center justify-between text-xs text-muted-foreground">
        <span>{mem(d.used_memory_mb)} / {mem(d.total_memory_mb)}</span>
        <span>{d.temperature_celsius != null ? `${Math.round(d.temperature_celsius)}°C` : "—"}</span>
      </div>
    </div>
  );
}

export function DeviceBar() {
  const data = useEventStream<DevicesResponse>("/api/devices/stream");
  if (!data) return <p className="text-sm text-muted-foreground">设备加载中…</p>;
  const devices = data.data ?? [];
  if (devices.length === 0) return <p className="text-sm text-muted-foreground">未检测到设备</p>;
  return (
    <div className="flex flex-wrap gap-3">
      {devices.map((d) => (
        <DeviceCard key={d.device_name} d={d} />
      ))}
    </div>
  );
}
