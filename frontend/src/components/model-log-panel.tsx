import { LogLines } from "@/components/logs/log-lines";
import type { ModelInfo } from "@/lib/api";
import { useModelLogs } from "@/lib/use-model-logs";

/** 右栏日志面板(选中模型)。头部(状态点/别名/端口) + 共享 LogLines。 */
export function ModelLogPanel({ m }: { m: ModelInfo }) {
  const h = useModelLogs(m.alias, m.pid);   // pid 作 runKey:停止/重启时重连并清空
  const dotColor = m.status === "routing" ? "var(--color-success)" : "var(--color-muted-foreground)";
  return (
    <div className="flex h-full flex-col overflow-hidden rounded-lg border border-border bg-card">
      <div className="flex items-center justify-between border-b border-border px-3.5 py-2">
        <span className="text-[12px] font-semibold text-foreground">
          <span className="mr-1 inline-block size-[7px] rounded-full align-middle" style={{ background: dotColor }} />
          {m.alias}
          <span className="ml-2 text-[10.5px] font-normal text-muted-foreground">
            {m.mode} · :{m.port} · pid {m.pid ?? "—"}
          </span>
        </span>
      </div>
      <LogLines h={h} />
    </div>
  );
}
