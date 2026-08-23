import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { fetchSessions } from "@/lib/api";
import type { LogSession } from "@/lib/api";
import { NavTab, NavTabs } from "@/components/ui/nav-tabs";
import { SessionList } from "@/components/logs/session-list";
import { LogViewer } from "@/components/logs/log-viewer";
import { Loading } from "@/components/ui/card";
import { qk } from "@/lib/api/keys";

type Tab = LogSession["type"];

/** 日志查看页:双 Tab(系统/模型)+ 左会话列表 + 右行详情。 */
export default function LogsPage() {
  const [tab, setTab] = useState<Tab>("system");
  const [model, setModel] = useState<string>("");
  const [selectedId, setSelectedId] = useState<number | null>(null);

  // 模型下拉选项:仅来自未过滤的 type=model 列表(无 model 参数),每次进入模型 Tab 刷新。
  // 若取过滤后响应,选 "m1" 后选项会收缩成 ["m1"],选无会话的模型会清空 —— 违反
  // §8"稳定历史派生列表"。含已删模型的残留 alias,后端按会话历史回退解析(不再 404)。
  const modelsQ = useQuery({
    queryKey: qk.sessionModelOptions,
    queryFn: () => fetchSessions({ type: "model", limit: 50 }),
    enabled: tab === "model",
  });
  const models = Array.from(new Set((modelsQ.data ?? []).map((x) => x.alias).filter((a): a is string => !!a)));

  // 会话列表:随 Tab / 模型筛选变化,每 8s 轮询刷新(新会话/行数/状态实时感)。
  // 选中会话保持(仍存在则不动;被保留策略清掉或首载时选第一个)。
  // 404(如选项取到后会话被保留策略清掉)走 catch → 空列表。
  const sessionsQ = useQuery({
    queryKey: qk.sessionsList(tab, model),
    queryFn: () =>
      fetchSessions(
        tab === "system" ? { type: "system", limit: 50 }
          : { type: "model", model: model || undefined, limit: 50 },
      ),
    refetchInterval: 8000,
  });
  const sessions = useMemo(() => sessionsQ.data ?? [], [sessionsQ.data]);

  // 选中会话维护:列表刷新后,原选中仍存在则保持,否则回落列表首项(与旧轮询实现一致)。
  useEffect(() => {
    if (sessionsQ.isLoading) return;
    setSelectedId((prev) => {
      if (prev != null && sessions.some((x) => x.id === prev)) return prev;
      return sessions.length > 0 ? sessions[0].id : null;
    });
  }, [sessions, sessionsQ.isLoading]);

  const selected = sessions.find((s) => s.id === selectedId) ?? null;

  return (
    <div className="flex h-full flex-col gap-3">
      {/* Tab 栏(NavTabs 与系统页分区导航同款,防样式漂移) */}
      <NavTabs>
        {(["system", "model"] as const).map((t) => (
          <NavTab key={t} active={tab === t} onClick={() => setTab(t)}>
            {t === "system" ? "系统日志" : "模型日志"}
          </NavTab>
        ))}
        {tab === "model" && (
          <select value={model} onChange={(e) => setModel(e.target.value)}
            className="ml-auto rounded border border-border bg-background px-2 py-1 text-ui text-foreground">
            <option value="">全部模型</option>
            {models.map((m) => <option key={m} value={m}>{m}</option>)}
          </select>
        )}
      </NavTabs>
      {/* 主从。右列 minmax(0,1fr):grid 1fr 轨道 min 为 auto,日志行再折行也会把
          列撑宽导致页面横向膨胀;minmax(0,·) 强制轨道不缩于 0(行内折行/滚动)。 */}
      <div className="grid min-h-0 flex-1 grid-cols-[260px_minmax(0,1fr)] gap-3">
        <div className="overflow-y-auto rounded-lg border border-border bg-card">
          {sessionsQ.isLoading ? <div className="p-4"><Loading /></div>
            : <SessionList sessions={sessions} selectedId={selectedId} onSelect={setSelectedId} />}
        </div>
        <div className="min-h-0">
          {selected ? <LogViewer key={selected.id} session={selected} /> : (
            <div className="flex h-full items-center justify-center rounded-lg border border-dashed border-border text-sm text-muted-foreground">
              选择左侧会话查看日志
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
