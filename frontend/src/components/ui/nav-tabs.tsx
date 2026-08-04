import type { ReactNode } from "react";

/** 页面级分区导航(日志双 Tab / 系统五 zone 共用同一语言,防样式漂移):
 * 胶囊容器 + accent/12 激活。children 内含 NavTab 与可选右对齐控件(如日志页模型下拉 ml-auto)。 */
export function NavTabs({ children }: { children: ReactNode }) {
  return (
    <div className="flex flex-wrap items-center gap-1 rounded-lg border border-border bg-card p-1 text-sm shadow-card">
      {children}
    </div>
  );
}

export function NavTab({
  active,
  onClick,
  children,
  className = "",
}: {
  active: boolean;
  onClick: () => void;
  children: ReactNode;
  className?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-current={active ? "true" : undefined}
      className={`rounded-md px-3 py-1 font-medium transition-colors ${
        active
          ? "bg-primary-accent/12 text-primary-accent"
          : "text-muted-foreground hover:text-foreground"
      } ${className}`}
    >
      {children}
    </button>
  );
}
