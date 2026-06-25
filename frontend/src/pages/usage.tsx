import { PageHeader } from "@/components/page-header";

/** 用量统计 — token analytics (per-model, time-series). Stub; Stage 3. */
export default function UsagePage() {
  return (
    <>
      <PageHeader title="用量统计" subtitle="token 用量分析 · 按模型 · 时间序列" />
      <div className="rounded-lg border border-dashed border-border p-16 text-center text-sm text-muted-foreground">
        建设中
      </div>
    </>
  );
}
