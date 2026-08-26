import { useState } from "react";

import { UsageByModelTable } from "@/components/usage-by-model-table";
import { UsageChartCard } from "@/components/usage-chart-card";
import { UsageKpiRow } from "@/components/usage-kpi-row";
import { UsageRangePicker } from "@/components/usage-range-picker";
import { USAGE_REFETCH, paramsForState, type UsageRangeState } from "@/lib/usage-range";

/** 用量统计 — token 用量分析(KPI + 时间序列 + 按模型)。 */
export default function UsagePage() {
  const [range, setRange] = useState<UsageRangeState>({ preset: "7d", custom: null });
  const params = paramsForState(range);
  const refetch = USAGE_REFETCH[range.preset];

  return (
    <>
      <div className="mb-6 flex justify-end">
        <UsageRangePicker value={range} onChange={setRange} />
      </div>
      <section className="mb-6">
        <UsageKpiRow params={params} refetch={refetch} />
      </section>
      <section className="mb-6">
        <UsageChartCard params={params} preset={range.preset} custom={range.custom} refetch={refetch} />
      </section>
      <section>
        <UsageByModelTable params={params} refetch={refetch} />
      </section>
    </>
  );
}
