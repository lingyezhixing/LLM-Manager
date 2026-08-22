import type { ReactNode } from "react";

/** 统一卡片壳:rounded-lg border bg-card p-4 shadow-card(各页面卡片共用,防样式漂移)。 */
export function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <div className={`rounded-lg border border-border bg-card p-4 shadow-card ${className}`}>{children}</div>;
}

/** 加载占位:统一「加载中…」文案与字号。 */
export function Loading({ label = "加载中…" }: { label?: string }) {
  return <p className="text-sm text-muted-foreground">{label}</p>;
}

/** 骨架占位:n 行文本骨架(宽度递减造层级;温和脉冲 2s,reduced-motion 由全局规则降级为静止)。 */
export function Skeleton({ rows = 3, className = "" }: { rows?: number; className?: string }) {
  return (
    <div className={`flex flex-col gap-2 ${className}`} aria-hidden>
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="h-4 animate-skeleton rounded bg-muted"
          style={{ width: `${88 - i * 18}%` }} />
      ))}
    </div>
  );
}

/** 空态占位:虚线框 + 居中提示 + 可选引导动作(图表区/列表空时用)。 */
export function Empty({ label, action, className = "" }: {
  label: string; className?: string; action?: ReactNode;
}) {
  return (
    <div className={`flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed
      border-border px-6 py-8 text-sm text-muted-foreground ${className}`}>
      <span>{label}</span>
      {action}
    </div>
  );
}
