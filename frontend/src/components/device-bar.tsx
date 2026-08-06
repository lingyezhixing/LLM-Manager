import { useState } from "react";
import { Cpu } from "lucide-react";
import { useEventStream } from "@/lib/hooks/use-event-stream";
import type { DevicesResponse } from "@/lib/api";

type MemUnit = "GB" | "MB";

/** Real-time device bar (概览 top). Subscribes to /api/devices/stream (2s push).
 *  利用率 + 显存/内存占用双条形;点击内存读数切换单位(全部卡片联动)。
 *  默认 MB(用户偏好),点击切 GB。 */
function mem(mb: number, unit: MemUnit): string {
  return unit === "MB" ? `${Math.round(mb)} MB` : `${(mb / 1024).toFixed(1)} GB`;
}

function pctOf(used: number, total: number): number {
  return total > 0 ? Math.min(100, Math.max(0, (used / total) * 100)) : 0;
}

function DeviceCard({
  d,
  unit,
  onToggleUnit,
}: {
  d: DevicesResponse["data"][number];
  unit: MemUnit;
  onToggleUnit: () => void;
}) {
  const isCpu = d.device_type === "CPU";
  return (
    <div className="min-w-[150px] flex-1 rounded-lg border border-border-subtle bg-card-2 p-3">
      <div className="flex items-center justify-between text-sm font-medium">
        <span className="truncate">{d.device_name}</span>
        <span className="text-xs text-muted-foreground">
          {isCpu ? "CPU" : "GPU"}
          {d.temperature_celsius != null ? <> · <span className="font-mono">{Math.round(d.temperature_celsius)}°C</span></> : ""}
          {d.freq_mhz != null ? <> · <span className="font-mono">{Math.round(d.freq_mhz)} MHz</span></> : ""}
        </span>
      </div>
      <div className="mt-2 flex items-center justify-between text-xs text-muted-foreground">
        <span>利用率</span>
        <span className="font-mono tabular-nums">{Math.round(d.usage_percentage)}%</span>
      </div>
      <div className="mt-1 h-1 overflow-hidden rounded bg-muted">
        <div
          className="h-full bg-primary transition-[width] duration-300"
          style={{ width: `${Math.min(100, Math.max(0, d.usage_percentage))}%` }}
        />
      </div>
      {/* 显存/内存占用:读数(点击切 MB/GB)+ 占用条形(used/total,绿色区别于利用率主色) */}
      <div className="mt-2 flex items-center justify-between text-xs text-muted-foreground">
        <span>{isCpu ? "内存占用" : "显存占用"}</span>
        <button
          type="button"
          onClick={onToggleUnit}
          title={`点击切换 ${unit === "MB" ? "GB" : "MB"}`}
          className="cursor-pointer rounded font-mono tabular-nums transition-colors hover:text-foreground"
        >
          {mem(d.used_memory_mb, unit)} / {mem(d.total_memory_mb, unit)}
        </button>
      </div>
      <div className="mt-1 h-1 overflow-hidden rounded bg-muted">
        <div
          className="h-full bg-success transition-[width] duration-300"
          style={{ width: `${pctOf(d.used_memory_mb, d.total_memory_mb)}%` }}
        />
      </div>
    </div>
  );
}

export function DeviceBar() {
  const data = useEventStream<DevicesResponse>("/api/devices/stream");
  const [unit, setUnit] = useState<MemUnit>("MB");   // 默认 MB,点击读数切 GB

  const toggle = () => setUnit((u) => (u === "GB" ? "MB" : "GB"));
  const devices = data?.data ?? [];

  return (
    <section className="rounded-lg border border-border bg-card p-4 shadow-card">
      <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-foreground">
        <Cpu className="size-4 text-primary-accent" />
        设备
      </h3>
      {!data ? (
        <p className="text-sm text-muted-foreground">设备加载中…</p>
      ) : devices.length === 0 ? (
        <p className="text-sm text-muted-foreground">未检测到设备</p>
      ) : (
        <div className="flex flex-wrap gap-3">
          {devices.map((d) => (
            <DeviceCard key={d.device_name} d={d} unit={unit} onToggleUnit={toggle} />
          ))}
        </div>
      )}
    </section>
  );
}
