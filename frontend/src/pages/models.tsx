import { useState } from "react";
import { useEventStream } from "@/lib/use-event-stream";
import { useNowTick } from "@/lib/use-now";
import { ModelCard } from "@/components/model-card";
import { ModelLogPanel } from "@/components/model-log-panel";
import { PageHeader } from "@/components/page-header";
import type { ModelsResponse } from "@/lib/api";

/**
 * 模型管理 — 操作控制台。左 1 : 右 4:左栏模型卡片(三合一启停按钮 + 状态/模式/请求数,
 * 点卡片体选中),右栏选中模型的实时日志面板。状态走 /api/models/stream SSE;日志走
 * /api/models/{alias}/logs/stream SSE;启停走 POST /start|/stop。
 */
export default function ModelsPage() {
  const data = useEventStream<ModelsResponse>("/api/models/stream");
  const now = useNowTick(1000);
  const [sel, setSel] = useState<string | null>(null);
  const models = data?.data ?? [];
  const selected = models.find((mm) => mm.alias === sel) ?? models[0] ?? null;

  return (
    <>
      <PageHeader title="模型管理" subtitle="模型列表 · 启停控制 · 实时日志" />
      <div className="grid h-[calc(100vh-180px)] gap-3 lg:grid-cols-[minmax(0,1fr)_minmax(0,4fr)]">
        <div className="flex flex-col gap-2 overflow-auto">
          {models.length === 0
            ? <p className="text-sm text-muted-foreground">加载中…</p>
            : models.map((m) => (
              <ModelCard key={m.alias} m={m} nowMs={now}
                selected={selected?.alias === m.alias} onSelect={() => setSel(m.alias)} />
            ))}
        </div>
        {selected
          ? <ModelLogPanel m={selected} />
          : <div className="rounded-lg border border-border p-16 text-center text-sm text-muted-foreground">选择左侧模型查看日志</div>}
      </div>
    </>
  );
}
