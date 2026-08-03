import type { ReactNode } from "react";

/** 圆角边框 label+value 信息块(概览/用量/系统页共用;原 4 处本地 Tile 单源)。
 *  value 为 ReactNode:用量页成本块把 ErrorState 嵌在 tile 值内(错误也带「成本」标签边框)。 */
export function InfoTile({ label, value, valueClass = "", className = "" }: {
  label: string;
  value: ReactNode;
  valueClass?: string;
  className?: string;
}) {
  return (
    <div className={`rounded-lg border border-border px-3 py-2 ${className}`}>
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className={`mt-0.5 text-base font-semibold ${valueClass}`}>{value}</div>
    </div>
  );
}
