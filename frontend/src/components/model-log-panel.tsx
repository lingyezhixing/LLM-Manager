import { LogLines } from "@/components/logs/log-lines";
import type { ModelInfo } from "@/lib/api";
import { useModelLogs } from "@/lib/hooks/use-model-logs";

// 活跃态(有进程可看日志):启动中→服务中全程;stopped/failed 无进程,不展示历史日志。
const ACTIVE_STATUSES = ["routing", "starting", "init_script", "health_check"];

/** 右栏日志面板(选中模型)。头部(状态点/别名/端口) + 共享 LogLines;
 * 模型未运行(stopped/failed)时不展示历史日志(避免误以为仍在运行),显示空态。 */
export function ModelLogPanel({ m }: { m: ModelInfo }) {
  const active = ACTIVE_STATUSES.includes(m.status);
  const h = useModelLogs(m.alias, m.pid, active);   // pid 作 runKey:停止/重启时重连并清空
  const dotColor = active ? "var(--color-success)" : "var(--color-muted-foreground)";
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
      {active
        ? <LogLines h={h} />
        : <div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">模型未运行,无日志</div>}
    </div>
  );
}
