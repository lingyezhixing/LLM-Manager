import { ModelsDashboard } from "@/components/models-dashboard";
import { PageHeader } from "@/components/page-header";

/**
 * 概览 — cross-cutting overview (read-only).
 * Stage 2 temporarily hosts the model list here; Stage 3 replaces it with the real
 * overview (device bar + status cards + token summary) and relocates the list to 模型管理.
 */
export default function OverviewPage() {
  return (
    <>
      <PageHeader title="概览" subtitle="模型状态总览 · 设备与用量快照" />
      <ModelsDashboard />
    </>
  );
}
