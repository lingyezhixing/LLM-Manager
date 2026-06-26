import { DeviceBar } from "@/components/device-bar";
import { ModelSummary } from "@/components/model-summary";
import { PageHeader } from "@/components/page-header";
import { SessionStats } from "@/components/session-stats";

/**
 * 概览 — read-only cross-cutting overview.
 * Order: device bar (SSE live) → token row (curve placeholder [Round 2] + session stats)
 * → model status summary (refetch).
 */
export default function OverviewPage() {
  return (
    <>
      <PageHeader title="概览" subtitle="模型状态总览 · 设备与用量快照" />

      <section className="mb-6">
        <h2 className="mb-2 text-xs uppercase tracking-wide text-muted-foreground">设备</h2>
        <DeviceBar />
      </section>

      <section className="mb-6 grid gap-4 lg:grid-cols-[minmax(0,1fr)_300px]">
        <div className="rounded-lg border border-dashed border-border p-12 text-center text-sm text-muted-foreground">
          Token 消耗曲线(建设中 · Round 2)
        </div>
        <div className="rounded-lg border border-border p-4">
          <SessionStats />
        </div>
      </section>

      <section className="mb-6">
        <ModelSummary />
      </section>
    </>
  );
}
