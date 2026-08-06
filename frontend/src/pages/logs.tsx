import { useEffect, useMemo, useState } from "react";
import { fetchSessions } from "@/lib/api";
import type { LogSession } from "@/lib/api";
import { NavTab, NavTabs } from "@/components/ui/nav-tabs";
import { SessionList } from "@/components/logs/session-list";
import { LogViewer } from "@/components/logs/log-viewer";

type Tab = LogSession["type"];

/** 日志查看页:双 Tab(系统/模型)+ 左会话列表 + 右行详情。 */
export default function LogsPage() {
  const [tab, setTab] = useState<Tab>("system");
  const [models, setModels] = useState<string[]>([]);   // 模型下拉选项(alias,来自会话历史)
  const [model, setModel] = useState<string>("");
  const [sessions, setSessions] = useState<LogSession[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);

  // 模型下拉选项:仅来自未过滤的 type=model 列表(无 model 参数),每次进入模型 Tab 刷新。
  // 若取过滤后响应,选 "m1" 后选项会收缩成 ["m1"],选无会话的模型会清空 —— 违反
  // §8"稳定历史派生列表"。含已删模型的残留 alias,后端按会话历史回退解析(不再 404)。
  useEffect(() => {
    if (tab !== "model") return;
    fetchSessions({ type: "model", limit: 50 })
      .then((s) => {
        setModels(Array.from(new Set(s.map((x) => x.alias).filter((a): a is string => !!a))));
      })
      .catch(() => { /* 后端不可达:保留原选项 */ });
  }, [tab]);

  // 会话列表:随 Tab / 模型筛选变化,每 8s 轮询刷新(新会话/行数/状态实时感)。
  // 选中会话保持(仍存在则不动;被保留策略清掉或首载时选第一个)。
  // 404(如选项取到后会话被保留策略清掉)走 catch → 空列表。
  useEffect(() => {
    let cancelled = false;
    const load = () => {
      fetchSessions(tab === "system" ? { type: "system", limit: 50 }
        : { type: "model", model: model || undefined, limit: 50 })
        .then((s) => {
          if (cancelled) return;
          setSessions(s);
          setSelectedId((prev) => {
            if (prev != null && s.some((x) => x.id === prev)) return prev;
            return s.length > 0 ? s[0].id : null;
          });
        })
        .catch(() => { /* 后端不可达或未知 alias 404:留空列表 */ })
        .finally(() => { if (!cancelled) setLoading(false); });
    };
    setSessions([]); setSelectedId(null);
    setLoading(true);
    load();
    const timer = setInterval(load, 8000);
    return () => { cancelled = true; clearInterval(timer); };
  }, [tab, model]);

  const selected = useMemo(
    () => sessions.find((s) => s.id === selectedId) ?? null, [sessions, selectedId]);

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
            className="ml-auto rounded border border-border bg-background px-2 py-1 text-[11px] text-foreground">
            <option value="">全部模型</option>
            {models.map((m) => <option key={m} value={m}>{m}</option>)}
          </select>
        )}
      </NavTabs>
      {/* 主从 */}
      <div className="grid min-h-0 flex-1 grid-cols-[260px_1fr] gap-3">
        <div className="overflow-y-auto rounded-lg border border-border bg-card">
          {loading ? <div className="p-4 text-center text-xs text-muted-foreground">加载中…</div>
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
