/** 工具箱 — 操作性工具:网络唤醒(WOL)+ Claude Code 预设。NavTabs zone 模式,同系统页。 */
import { useState } from "react";
import { ClaudePanel } from "@/components/tools/claude-panel";
import { ToolsNav, type ToolsZone } from "@/components/tools/tools-nav";
import { WolPanel } from "@/components/tools/wol-panel";

export default function ToolsPage() {
  const [zone, setZone] = useState<ToolsZone>("network");
  return (
    <div>
      <ToolsNav active={zone} onSelect={setZone} />
      <div className="mt-6">
        {zone === "network" && <WolPanel />}
        {zone === "claude" && <ClaudePanel />}
      </div>
    </div>
  );
}
