import type { ReactNode } from "react";

/** 统一卡片壳:rounded-lg border bg-card p-4 shadow-card(各页面卡片共用,防样式漂移)。 */
export function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <div className={`rounded-lg border border-border bg-card p-4 shadow-card ${className}`}>{children}</div>;
}

/** 加载占位:统一「加载中…」文案与字号。 */
export function Loading({ label = "加载中…" }: { label?: string }) {
  return <p className="text-sm text-muted-foreground">{label}</p>;
}

/** 空态占位:虚线框 + 居中提示(图表区/列表空时用)。 */
export function Empty({ label, className = "" }: { label: string; className?: string }) {
  return (
    <div className={`flex items-center justify-center rounded-lg border border-dashed border-border text-sm text-muted-foreground ${className}`}>
      {label}
    </div>
  );
}
