import { NavTab, NavTabs } from "@/components/ui/nav-tabs";

export type ToolsZone = "network" | "claude";

const ZONES: { key: ToolsZone; label: string }[] = [
  { key: "network", label: "网络唤醒" },
  { key: "claude", label: "Claude Code" },
];

/** 工具箱分区导航:与系统页/日志页同款 NavTabs(胶囊 + accent/12 激活),防样式漂移。 */
export function ToolsNav({
  active,
  onSelect,
}: {
  active: ToolsZone;
  onSelect: (zone: ToolsZone) => void;
}) {
  return (
    <NavTabs>
      {ZONES.map((z) => (
        <NavTab key={z.key} active={z.key === active} onClick={() => onSelect(z.key)}>
          {z.label}
        </NavTab>
      ))}
    </NavTabs>
  );
}
