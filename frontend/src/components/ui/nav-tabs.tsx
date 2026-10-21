import type { ReactNode } from "react";

/** 页面级分区导航(日志双 Tab / 系统五 zone 共用同一语言,防样式漂移):
 * 胶囊容器 + accent/12 激活。children 内含 NavTab 与可选右对齐控件(如日志页模型下拉 ml-auto)。
 * sticky 吸顶:长内容滚动时保持可见(top 让开壳层 PillBar 的吸顶区 72px;bg-pill + blur
 * 与 PillBar 同款玻璃,内容滚过时压暗模糊,不糊眼)。 */
export function NavTabs({ children }: { children: ReactNode }) {
  return (
    <div className="sticky top-[72px] z-20 flex flex-wrap items-center gap-1 rounded-lg border border-border-subtle bg-pill p-1 text-sm shadow-card backdrop-blur-lg">
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

/** 控制台内嵌分区导航:底部划线式(无胶囊/无底色),整行一条分隔线与子页分开。
 *  比页面级 NavTabs 更轻 —— 固定高度控制台里不需要再套一层玻璃容器。 */
export function NavTabRow({ children }: { children: ReactNode }) {
  return (
    <div className="flex shrink-0 items-center gap-5 border-b border-border-subtle">{children}</div>
  );
}

/** 划线式页签:未选中灰字,选中变蓝 + 2px 底线(负 margin 压住容器的 1px 分隔线)。 */
export function NavTabUnderline({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-current={active ? "true" : undefined}
      className={`-mb-px border-b-2 pb-2 pt-1 text-sm transition-colors ${
        active
          ? "border-primary-accent font-medium text-primary-accent"
          : "border-transparent text-muted-foreground hover:text-foreground"
      }`}
    >
      {children}
    </button>
  );
}

/** 通用分区导航(系统页 / 工具箱页共用,防样式漂移):zone key → label 映射渲染 NavTab。 */
export function ZoneNav<K extends string>({
  zones,
  active,
  onSelect,
}: {
  zones: readonly { key: K; label: string }[];
  active: K;
  onSelect: (zone: K) => void;
}) {
  return (
    <NavTabs>
      {zones.map((z) => (
        <NavTab key={z.key} active={z.key === active} onClick={() => onSelect(z.key)}>
          {z.label}
        </NavTab>
      ))}
    </NavTabs>
  );
}
