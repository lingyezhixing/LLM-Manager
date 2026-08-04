import type { LogSession } from "@/lib/api";
import { fmtDuration, fmtTime } from "@/lib/format";

/** 左栏会话列表。running 徽标 + 时长 + 行数;选中高亮。 */
export function SessionList({
  sessions, selectedId, onSelect,
}: { sessions: LogSession[]; selectedId: number | null; onSelect: (id: number) => void }) {
  if (sessions.length === 0) {
    return <div className="p-4 text-center text-xs text-muted-foreground">暂无会话</div>;
  }
  return (
    <div className="flex flex-col gap-1 p-2">
      {sessions.map((s) => {
        const active = s.id === selectedId;
        const running = s.status === "running";
        return (
          <button key={s.id} onClick={() => onSelect(s.id)}
            className={`rounded-md border px-2.5 py-1.5 text-left transition-colors ${
              active ? "border-primary-accent bg-primary-accent/10" : "border-border-subtle bg-card-2 hover:bg-card-hover"
            }`}>
            <div className="flex items-center gap-1.5 text-[11px] font-medium text-foreground">
              {running && <span className="size-1.5 shrink-0 rounded-full bg-success" />}
              <span className="tabular-nums">{fmtTime(s.start_time)}</span>
              <span className="ml-auto font-mono text-[10px] text-muted-foreground">{s.line_count} 行</span>
            </div>
            <div className="text-[10px] text-muted-foreground">
              {running ? "进行中" : `时长 ${fmtDuration(s.duration_s ?? 0)}`}
            </div>
          </button>
        );
      })}
    </div>
  );
}
