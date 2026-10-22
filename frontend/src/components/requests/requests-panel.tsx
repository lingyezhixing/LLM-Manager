import { useEffect, useMemo, useState } from "react";
import { RequestDetail } from "@/components/requests/request-detail";
import { RequestList } from "@/components/requests/request-list";
import { Loading } from "@/components/ui/card";
import type { RequestRecord, RequestsResponse } from "@/lib/api";
import { useEventStream } from "@/lib/hooks/use-event-stream";
import { streamErrorText } from "@/lib/format";

const ACTIVE: ReadonlySet<RequestRecord["status"]> = new Set(["running", "cancelling"]);

/** 运行中按开始时间升序在前;已取消次之;其余终态按结束时间倒序。 */
function sortRequests(items: RequestRecord[]): RequestRecord[] {
  const byStart = (a: RequestRecord, b: RequestRecord) => a.started_at - b.started_at;
  const byEndDesc = (a: RequestRecord, b: RequestRecord) =>
    (b.finished_at ?? 0) - (a.finished_at ?? 0);
  return [
    ...items.filter((i) => ACTIVE.has(i.status)).sort(byStart),
    ...items.filter((i) => i.status === "cancelled").sort(byEndDesc),
    ...items.filter((i) => !ACTIVE.has(i.status) && i.status !== "cancelled").sort(byEndDesc),
  ];
}

/**
 * 请求区(模型管理页右区)。订阅全局请求流,按 alias(served name)过滤。
 * 宽屏:列表 + 详情并排;窄屏(<xl):上下堆叠(不引 Dialog —— 与模型页既有响应式一致)。
 */
export function RequestsPanel({ alias, routing }: { alias: string; routing: boolean }) {
  const { data, error } = useEventStream<RequestsResponse>("/api/requests/stream");
  const items = useMemo(
    () => sortRequests((data?.data ?? []).filter((r) => r.alias === alias)),
    [data, alias],
  );
  const [selectedId, setSelectedId] = useState<string | null>(null);

  // 选中回落:原选中仍在则保持,否则取列表首项(新请求冒出来不抢焦点)。
  useEffect(() => {
    setSelectedId((prev) =>
      prev && items.some((i) => i.id === prev) ? prev : (items[0]?.id ?? null),
    );
  }, [items]);

  if (error) {
    return <p className="text-sm text-muted-foreground">{streamErrorText("请求列表")}</p>;
  }
  if (data === null) {
    return (
      <div className="animate-swap-in">
        <Loading />
      </div>
    );
  }

  const selected = items.find((i) => i.id === selectedId) ?? null;

  return (
    <div className="grid h-full min-h-0 flex-1 grid-rows-[minmax(0,40%)_minmax(0,1fr)] gap-2 xl:grid-cols-[240px_minmax(0,1fr)] xl:grid-rows-1">
      <RequestList
        alias={alias}
        items={items}
        selectedId={selectedId}
        onSelect={setSelectedId}
        routing={routing}
      />
      <div key={selected?.id ?? "empty"} className="animate-swap-in min-h-0">
        {selected ? (
          <RequestDetail id={selected.id} record={selected} />
        ) : (
          <div className="flex h-full items-center justify-center rounded-lg border border-dashed border-border text-xs text-muted-foreground">
            选择左侧请求查看详情
          </div>
        )}
      </div>
    </div>
  );
}
