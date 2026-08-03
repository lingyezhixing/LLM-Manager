import { LogLines } from "@/components/logs/log-lines";
import type { LogSession } from "@/lib/api";
import { useSessionLogs } from "@/lib/use-model-logs";

/** 右栏日志行详情:头部 + 共享 LogLines(级别过滤/搜索跳转/实时跟随)。 */
export function LogViewer({ session }: { session: LogSession }) {
  const h = useSessionLogs(session.id);   // key=session.id 由父级保证重建
  return (
    <div className="flex h-full flex-col overflow-hidden rounded-lg border border-border bg-card">
      <div className="flex items-center justify-between border-b border-border px-3.5 py-2">
        <span className="text-[12px] font-semibold text-foreground">
          {session.alias ?? "系统日志"}
          <span className="ml-2 text-[10.5px] font-normal text-muted-foreground">
            #{session.id} · {session.status === "running" ? "进行中" : "已结束"} · {session.line_count} 行
          </span>
        </span>
      </div>
      <LogLines h={h} />
    </div>
  );
}
