import type { RequestRecord } from "@/lib/api";
import { clearCancelledRequests } from "@/lib/api";
import { errMsg, fmtDuration, formatTokens } from "@/lib/format";
import { useConfirm } from "@/lib/hooks/use-confirm";
import { useNowTick } from "@/lib/hooks/use-now-tick";
import { useToast } from "@/lib/hooks/use-toast";

const DOT: Record<RequestRecord["status"], string> = {
  running: "var(--color-success)",
  cancelling: "var(--color-warning)",
  completed: "var(--color-muted-foreground)",
  cancelled: "var(--color-destructive)",
  client_closed: "var(--color-warning)",
  failed: "var(--color-destructive)",
};

const ACTIVE: ReadonlySet<RequestRecord["status"]> = new Set(["running", "cancelling"]);

const shortPath = (p: string) => p.replace(/^\/v1\//, "");

function elapsedSec(r: RequestRecord, nowMs: number): number {
  const end = r.finished_at ?? nowMs / 1000;
  return Math.max(0, Math.round(end - r.started_at));
}

function Card({
  r,
  selected,
  nowMs,
  onSelect,
}: {
  r: RequestRecord;
  selected: boolean;
  nowMs: number;
  onSelect: () => void;
}) {
  const tokens = r.usage ? `${formatTokens(r.usage.output_tokens)} tok` : null;
  const border = selected
    ? "border-primary-accent bg-primary-accent/12"
    : r.status === "cancelled"
      ? "border-destructive/30 hover:bg-card-hover"
      : "border-border-subtle bg-card-2 hover:bg-card-hover";
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-pressed={selected}
      className={`rounded-md border px-2.5 py-1.5 text-left transition-colors duration-(--motion-base) ${border}`}
    >
      <div className="truncate text-card-title font-medium text-foreground">
        {r.request_preview || "(无输入预览)"}
      </div>
      <div className="mt-1 flex items-center gap-1.5 text-dense text-muted-foreground">
        <span
          className={`size-[6px] shrink-0 rounded-full ${r.status === "running" ? "animate-dot-pulse" : ""}`}
          style={{ background: DOT[r.status] }}
        />
        <span className="truncate">{shortPath(r.path)}</span>
        <span className="ml-auto shrink-0 tabular-nums">
          {fmtDuration(elapsedSec(r, nowMs))}
          {tokens ? ` · ${tokens}` : ""}
        </span>
      </div>
    </button>
  );
}

function GroupHead({ label, count }: { label: string; count: number }) {
  return (
    <div className="mt-1 flex items-center gap-2 px-0.5 text-dense text-muted-foreground">
      <span>
        {label} {count}
      </span>
      <span className="h-px flex-1 bg-border-subtle" />
    </div>
  );
}

/** 请求列表(当前模型):活动 / 已取消 / 最近 三段。 */
export function RequestList({
  alias,
  items,
  selectedId,
  onSelect,
  routing,
}: {
  alias: string;
  items: RequestRecord[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  routing: boolean;
}) {
  const now = useNowTick(1000);
  const confirm = useConfirm();
  const toast = useToast();

  const active = items.filter((i) => ACTIVE.has(i.status));
  const cancelled = items.filter((i) => i.status === "cancelled");
  const recent = items.filter((i) => !ACTIVE.has(i.status) && i.status !== "cancelled");

  const clear = async () => {
    if (cancelled.length === 0) return;
    const ok = await confirm({
      title: "清空已取消的请求",
      description: `将删除当前模型（${alias}）的 ${cancelled.length} 条已取消记录，其他模型不受影响。`,
      confirmText: "清空",
    });
    if (!ok) return;
    try {
      await clearCancelledRequests(alias);
    } catch (e) {
      toast.error(errMsg(e));
    }
  };

  return (
    <div className="flex min-h-0 flex-col overflow-hidden rounded-lg border border-border-subtle">
      <div className="flex shrink-0 items-center gap-2 border-b border-border-subtle px-2.5 py-1.5 text-dense text-muted-foreground">
        <span>
          活动 {active.length} · 已取消 {cancelled.length} · 最近 {recent.length}
        </span>
        {cancelled.length > 0 && (
          <button
            type="button"
            onClick={clear}
            className="ml-auto rounded border border-destructive/40 px-1.5 py-px text-dense text-destructive hover:bg-destructive/10"
          >
            清空已取消
          </button>
        )}
      </div>

      <div className="flex min-h-0 flex-1 flex-col gap-1.5 overflow-y-auto p-2">
        {items.length === 0 && (
          <div className="flex flex-1 items-center justify-center px-3 text-center text-xs text-muted-foreground">
            {routing ? "暂无请求" : "模型未运行，无请求"}
          </div>
        )}
        {active.length > 0 && <GroupHead label="活动" count={active.length} />}
        {active.map((r) => (
          <Card
            key={r.id}
            r={r}
            selected={r.id === selectedId}
            nowMs={now}
            onSelect={() => onSelect(r.id)}
          />
        ))}
        {cancelled.length > 0 && <GroupHead label="已取消" count={cancelled.length} />}
        {cancelled.map((r) => (
          <Card
            key={r.id}
            r={r}
            selected={r.id === selectedId}
            nowMs={now}
            onSelect={() => onSelect(r.id)}
          />
        ))}
        {recent.length > 0 && <GroupHead label="最近" count={recent.length} />}
        {recent.map((r) => (
          <Card
            key={r.id}
            r={r}
            selected={r.id === selectedId}
            nowMs={now}
            onSelect={() => onSelect(r.id)}
          />
        ))}
      </div>
    </div>
  );
}
