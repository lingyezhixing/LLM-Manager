import { DeviceBar } from "@/components/device-bar";
import { ModelSummary } from "@/components/model-summary";
import { SessionStats } from "@/components/session-stats";
import { TokenCurveCard } from "@/components/token-curve-card";

/** 概览 — 扫读动线(v3.1 重排):第一屏「什么在跑(模型状态) + 跑了多久/花了多少(本次启动)」,
 *  设备条次之,趋势曲线收尾(最不紧迫的扫读信息)。 */
export default function OverviewPage() {
  return (
    <div className="flex flex-col gap-4">
      {/* 第一视觉锚点:状态对(模型 3fr | 本次启动账目 2fr) */}
      <div className="grid gap-4 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <ModelSummary />
        <SessionStats />
      </div>
      <DeviceBar />
      <TokenCurveCard />
    </div>
  );
}
