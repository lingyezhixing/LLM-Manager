import type { ReactNode } from "react";

/** 统一信息块:bg-card-2 底 + 等宽数值。value 为 ReactNode:
 *  用量页成本块把 ErrorState 嵌在 tile 值内(错误也带「成本」标签边框)。 */
export function InfoTile({ label, value, valueClass = "", className = "" }: {
  label: string;
  value: ReactNode;
  valueClass?: string;
  className?: string;
}) {
  return (
    <div className={`rounded-md border border-border-subtle bg-card-2 px-3 py-2 ${className}`}>
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className={`mt-0.5 font-mono text-base font-semibold tabular-nums ${valueClass}`}>{value}</div>
    </div>
  );
}
