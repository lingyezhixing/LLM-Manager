export type SystemZone = "general" | "models" | "network" | "claude" | "logs" | "info";

const ZONES: { key: SystemZone; label: string }[] = [
  { key: "general", label: "通用" },
  { key: "models", label: "模型" },
  { key: "network", label: "网络" },
  { key: "claude", label: "Claude" },
  { key: "logs", label: "日志" },
  { key: "info", label: "系统信息" },
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
                ? "bg-muted font-medium text-foreground"
                : "text-muted-foreground hover:bg-muted hover:text-foreground")
            }
          >
            {z.label}
          </button>
        );
      })}
    </nav>
  );
}
