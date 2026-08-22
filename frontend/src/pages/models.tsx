import { useState } from "react";
import { Link } from "react-router-dom";
import { Empty, Skeleton } from "@/components/ui/card";
import { useEventStream } from "@/lib/hooks/use-event-stream";
import { useNowTick } from "@/lib/hooks/use-now-tick";
import { ModelCard } from "@/components/model-card";
import { ModelLogPanel } from "@/components/model-log-panel";
import { byPort, streamErrorText } from "@/lib/format";
import type { ModelsResponse } from "@/lib/api";

/**
 * 模型管理 — 操作控制台。左 1 : 右 4:左栏模型卡片(三合一启停按钮 + 状态/模式/请求数,
 * 点卡片体选中),右栏选中模型的日志面板(数据来自持久会话 API /api/logs/*:按 alias
 * 定位最新会话并订阅 SSE 实时尾)。状态走 /api/models/stream SSE;启停走 POST /start|/stop。
 */
export default function ModelsPage() {
  const { data, error } = useEventStream<ModelsResponse>("/api/models/stream");
  const now = useNowTick(1000);
  const [sel, setSel] = useState<string | null>(null);
  const models = byPort(data?.data ?? []);
  const selected = models.find((mm) => mm.alias === sel) ?? models[0] ?? null;

  return (
    <>
      {/* 高度 = 视口 - 壳层 chrome(胶囊 40 + sticky top-4 16 + main pt-8 32 + pb 16 = 104px) */}
      <div className="grid h-[calc(100dvh-var(--shell-chrome))] gap-3 lg:grid-cols-[minmax(0,1fr)_minmax(0,4fr)]">
        <div className="flex flex-col gap-2 overflow-auto">
          {error
            ? <p className="text-sm text-muted-foreground">{streamErrorText("模型")}</p>
            : data === null
              ? <Skeleton rows={6} />
              : models.length === 0
                ? <Empty
                    label="暂无模型"
                    action={
                      <Link
                        to="/system"
                        className="rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground hover:opacity-90"
                      >
                        去系统配置添加
                      </Link>
                    }
                  />
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
