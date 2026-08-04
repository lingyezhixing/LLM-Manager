import { DeviceBar } from "@/components/device-bar";
import { ModelSummary } from "@/components/model-summary";
import { SessionStats } from "@/components/session-stats";
import { TokenCurveCard } from "@/components/token-curve-card";

/** 概览 — 卡片流:设备卡 → [曲线 2fr | 本次启动 1fr] → 模型摘要。 */
export default function OverviewPage() {
  return (
    <div className="flex flex-col gap-4">
      <DeviceBar />
      <div className="grid gap-4 lg:grid-cols-[minmax(0,4fr)_minmax(0,1fr)]">
        <TokenCurveCard />
        <SessionStats />
      </div>
      <ModelSummary />
    </div>
  );
}
