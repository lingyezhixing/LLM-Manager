import { useState } from "react";
import { Cpu } from "lucide-react";
import { Card, Empty, Skeleton } from "@/components/ui/card";
import { streamErrorText } from "@/lib/format";
import { useEventStream } from "@/lib/hooks/use-event-stream";
import type { DevicesResponse } from "@/lib/api";

type MemUnit = "GB" | "MB";

/** 实时设备栏(概览 top)。订阅 /api/devices/stream(2s 推送)。
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
    <div className="min-w-[150px] flex-1 rounded-md border border-border-subtle bg-card-2 p-3">
      <div className="flex items-center justify-between text-sm font-medium">
        <span className="truncate">{d.device_name}</span>
        <span className="text-xs text-muted-foreground">
          {d.freq_mhz != null ? (
            <span className="font-mono tabular-nums">{Math.round(d.freq_mhz)} MHz</span>
          ) : ""}
          {d.temperature_celsius != null ? (
            <>
              {d.freq_mhz != null ? " · " : ""}
              <span className="font-mono tabular-nums">{Math.round(d.temperature_celsius)}°C</span>
            </>
          ) : ""}
          {d.freq_mhz != null || d.temperature_celsius != null ? " · " : ""}
          {isCpu ? "CPU" : "GPU"}
        </span>
      </div>
      <div className="mt-2 flex items-center justify-between text-xs text-muted-foreground">
        <span>利用率</span>
        <span className="font-mono tabular-nums">{Math.round(d.usage_percentage)}%</span>
      </div>
      <div className="mt-1 h-1 overflow-hidden rounded bg-muted">
        <div
          className="h-full bg-primary transition-[width] duration-(--motion-slow)"
          style={{ width: `${Math.min(100, Math.max(0, d.usage_percentage))}%` }}
        />
      </div>
      {/* 显存/内存占用:读数(点击切 MB/GB)+ 占用条形(used/total,绿色区别于利用率主色) */}
      <div className="mt-2 flex items-center justify-between text-xs text-muted-foreground">
        <span>{isCpu ? "内存占用" : "显存占用"}</span>
        <button
          type="button"
          aria-pressed={unit === "GB"}
          onClick={onToggleUnit}
          title={`点击切换 ${unit === "MB" ? "GB" : "MB"}`}
          className="cursor-pointer rounded font-mono tabular-nums transition-colors duration-(--motion-base) hover:text-foreground"
        >
          {mem(d.used_memory_mb, unit)} / {mem(d.total_memory_mb, unit)}
        </button>
      </div>
      <div className="mt-1 h-1 overflow-hidden rounded bg-muted">
        <div
          className="h-full bg-success transition-[width] duration-(--motion-slow)"
          style={{ width: `${pctOf(d.used_memory_mb, d.total_memory_mb)}%` }}
        />
      </div>
    </div>
  );
}

export function DeviceBar() {
  const { data, error } = useEventStream<DevicesResponse>("/api/devices/stream");
  const [unit, setUnit] = useState<MemUnit>("MB");

  const toggle = () => setUnit((u) => (u === "GB" ? "MB" : "GB"));
  const devices = data?.data ?? [];

  return (
    <Card>
      <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-foreground">
        <Cpu className="size-4 text-primary-accent" />
        设备
      </h3>
      {error ? (
        <p className="text-sm text-muted-foreground">{streamErrorText("设备")}</p>
      ) : !data ? (
        <Skeleton rows={4} />
      ) : devices.length === 0 ? (
        <Empty label="未检测到设备" />
      ) : (
        <div className="flex flex-wrap gap-3">
          {devices.map((d) => (
            <DeviceCard key={d.device_name} d={d} unit={unit} onToggleUnit={toggle} />
          ))}
        </div>
      )}
    </Card>
  );
}
