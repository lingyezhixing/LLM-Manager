/** 工具箱 — 操作性工具:网络唤醒(WOL)+ Claude Code 预设。NavTabs zone 模式,同系统页。 */
import { useState } from "react";
import { ClaudePanel } from "@/components/tools/claude-panel";
import { WolPanel } from "@/components/tools/wol-panel";
import { ZoneNav } from "@/components/ui/nav-tabs";

type ToolsZone = "network" | "claude";
const ZONES: readonly { key: ToolsZone; label: string }[] = [
  { key: "network", label: "网络唤醒" },
  { key: "claude", label: "Claude Code" },
];

export default function ToolsPage() {
  const [zone, setZone] = useState<ToolsZone>("network");
  return (
    <div>
      <ZoneNav zones={ZONES} active={zone} onSelect={setZone} />
      <div className="mt-6">
        {zone === "network" && <WolPanel />}
        {zone === "claude" && <ClaudePanel />}
      </div>
    </div>
  );
}
