import { LogLines } from "@/components/logs/log-lines";
import { LogPane } from "@/components/logs/log-pane";
import type { LogSession } from "@/lib/api";
import { useSessionLogs } from "@/lib/hooks/use-model-logs";

/** 右栏日志行详情:头部 + 共享 LogLines(级别过滤/搜索跳转/实时跟随)。 */
export function LogViewer({ session }: { session: LogSession }) {
  const h = useSessionLogs(session.id);   // key=session.id 由父级保证重建
  return (
    <LogPane
      header={
        <span className="text-card-title font-semibold text-foreground">
          {session.alias ?? "系统日志"}
          <span className="ml-2 text-dense font-normal text-muted-foreground">
            #{session.id} · {session.status === "running" ? "进行中" : "已结束"} · {session.line_count} 行
          </span>
        </span>
      }
    >
      <LogLines h={h} />
    </LogPane>
  );
}
