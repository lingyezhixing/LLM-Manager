import { useState } from "react";
import { Link } from "react-router-dom";
import { Empty, Skeleton } from "@/components/ui/card";
import { buttonClasses } from "@/components/ui/button-variants";
import { NavTabRow, NavTabUnderline } from "@/components/ui/nav-tabs";
import { useEventStream } from "@/lib/hooks/use-event-stream";
import { useNowTick } from "@/lib/hooks/use-now-tick";
import { ModelCard } from "@/components/model-card";
import { ModelLogPanel } from "@/components/model-log-panel";
import { RequestsPanel } from "@/components/requests/requests-panel";
import { byPort, streamErrorText } from "@/lib/format";
import type { ModelsResponse } from "@/lib/api";

type Zone = "logs" | "requests";

/**
 * 模型管理 — 操作控制台。左 1 : 右 4:左栏模型卡片(三合一启停按钮 + 状态/模式/请求数,
 * 点卡片体选中),右区顶部切「进程日志 / 请求管理」两个 zone:
 * - 进程日志:选中模型的日志面板(持久会话 API + SSE 实时尾);
 * - 请求:内存请求簿记(列表 + 实时详情 + 单条取消)。
 * 选中态由本页持有,两个 zone 共用;zone 默认 logs(保持原有行为)。
 */
export default function ModelsPage() {
  const { data, error } = useEventStream<ModelsResponse>("/api/models/stream");
  const now = useNowTick(1000);
  const [sel, setSel] = useState<string | null>(null);
  const [zone, setZone] = useState<Zone>("logs");
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
                      <Link to="/system" className={buttonClasses("default", "sm")}>
                        去系统配置添加
                      </Link>
                    }
                  />
                : models.map((m, i) => (
              <ModelCard key={m.alias} m={m} nowMs={now} index={i}
                selected={selected?.alias === m.alias} onSelect={() => setSel(m.alias)}
                onOpenRequests={() => { setSel(m.alias); setZone("requests"); }} />
            ))}
        </div>
        <div className="flex min-h-0 flex-col gap-2">
          <NavTabRow>
            <NavTabUnderline active={zone === "logs"} onClick={() => setZone("logs")}>
              进程日志
            </NavTabUnderline>
            <NavTabUnderline active={zone === "requests"} onClick={() => setZone("requests")}>
              请求管理
              {selected && selected.pending > 0 && (
                <span className="ml-1.5 rounded-full bg-warning/15 px-1.5 py-px text-dense text-warning">
                  {selected.pending}
                </span>
              )}
            </NavTabUnderline>
          </NavTabRow>
          <div className="min-h-0 flex-1">
            {selected
              ? zone === "logs"
                ? <ModelLogPanel m={selected} />
                : <RequestsPanel alias={selected.alias} routing={selected.status === "routing"} />
              : <Empty label="选择左侧模型查看日志" className="h-full" />}
          </div>
        </div>
      </div>
    </>
  );
}
