export type SystemZone = "general" | "models" | "network" | "claude" | "database";

const ZONES: { key: SystemZone; label: string }[] = [
  { key: "general", label: "系统配置" },
  { key: "models", label: "模型配置" },
  { key: "network", label: "网络唤醒" },
  { key: "claude", label: "Claude Code" },
  { key: "database", label: "数据库管理" },
];

export function SystemNav({
  active, onSelect,
}: {
  active: SystemZone;
  onSelect: (zone: SystemZone) => void;
}) {
  return (
    <nav className="flex flex-wrap gap-2 border-b border-border pb-3">
      {ZONES.map((z) => {
        const isActive = z.key === active;
        return (
          <button
            key={z.key}
            type="button"
            onClick={() => onSelect(z.key)}
            className={
              "rounded-md px-3 py-2 text-sm transition-colors " +
              (isActive
                ? "bg-primary-accent/12 font-medium text-primary-accent"
                : "text-muted-foreground hover:bg-card-hover hover:text-foreground")
            }
          >
            {z.label}
          </button>
        );
      })}
    </nav>
  );
}
