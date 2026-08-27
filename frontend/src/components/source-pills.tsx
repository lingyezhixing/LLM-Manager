import type { UsageSource } from "@/lib/api";

const LABELS: { key: UsageSource; label: string }[] = [
  { key: "all", label: "全部" },
  { key: "local", label: "本地" },
  { key: "cloud", label: "云端" },
];

export function SourcePills({ value, onChange }: { value: UsageSource; onChange: (v: UsageSource) => void }) {
  return (
    <div className="flex items-center gap-1">
      {LABELS.map((o) => (
        <button key={o.key} type="button" aria-pressed={value === o.key} onClick={() => onChange(o.key)}
          className={`rounded-full px-2.5 py-0.5 text-ui transition-colors duration-(--motion-base) ${
            value === o.key ? "bg-primary-accent/12 font-medium text-primary-accent" : "text-muted-foreground hover:text-foreground"
          }`}>
          {o.label}
        </button>
      ))}
    </div>
  );
}
