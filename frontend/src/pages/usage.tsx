import { useState } from "react";

import { PageHeader } from "@/components/page-header";
import { UsageByModelTable } from "@/components/usage-by-model-table";
import { UsageChartCard } from "@/components/usage-chart-card";
import { UsageKpiRow } from "@/components/usage-kpi-row";
import {
  USAGE_REFETCH,
  UsageRangePicker,
  paramsForState,
  type UsageRangeState,
} from "@/components/usage-range-picker";

/** 用量统计 — token analytics (KPI + time-series + per-model). */
export default function UsagePage() {
  const [range, setRange] = useState<UsageRangeState>({ preset: "7d", custom: null });
  const params = paramsForState(range);
  const refetch = USAGE_REFETCH[range.preset];

  return (
    <>
      <PageHeader
        title="用量统计"
        subtitle="token 用量分析 · 按模型 · 时间序列"
        action={<UsageRangePicker value={range} onChange={setRange} />}
      />
      <section className="mb-6">
        <UsageKpiRow params={params} refetch={refetch} />
      </section>
      <section className="mb-6">
        <UsageChartCard params={params} preset={range.preset} refetch={refetch} />
      </section>
      <section className="mb-6">
        <UsageByModelTable params={params} refetch={refetch} />
      </section>
    </>
  );
}
