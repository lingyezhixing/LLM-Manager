import { NavTab, NavTabs } from "@/components/ui/nav-tabs";

export type SystemZone = "general" | "models" | "database";

const ZONES: { key: SystemZone; label: string }[] = [
  { key: "general", label: "系统配置" },
  { key: "models", label: "模型配置" },
  { key: "database", label: "数据库管理" },
];

/** 分区导航:NavTabs 与日志页双 Tab 同款(胶囊容器 + accent/12 激活),防样式漂移。 */
export function SystemNav({
  active, onSelect,
}: {
  active: SystemZone;
  onSelect: (zone: SystemZone) => void;
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
