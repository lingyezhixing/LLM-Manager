import type { ReactNode } from "react";

/**
 * 随父级宽度一起收起的文字(侧栏品牌、导航项、底部主题切换共用)。
 *
 * 两条约束,都是踩过坑的:
 * 1. 不条件渲染 —— grid-template-columns 1fr→0fr + overflow-hidden 让文字宽度跟着
 *    容器过渡收缩;条件渲染会让文字在宽度过渡开始前就消失。
 * 2. 间距用 margin 且只在展开态存在 —— 0 宽元素仍然会占 gap,折叠后会留下空档。
 */
export function CollapsingText({
  collapsed,
  gap,
  className = "",
  children,
}: {
  collapsed: boolean;
  /** 展开态的间距/伸缩类(如 "ml-3 flex-1");折叠态自动不生效。 */
  gap: string;
  className?: string;
  children: ReactNode;
}) {
  return (
    <span
      className={`grid transition-[grid-template-columns] duration-(--motion-base) ${
        collapsed ? "grid-cols-[0fr]" : `grid-cols-[1fr] ${gap}`
      }`}
    >
      <span
        className={`min-w-0 overflow-hidden whitespace-nowrap transition-opacity duration-(--motion-base) ${
          collapsed ? "opacity-0" : "opacity-100"
        } ${className}`}
      >
        {children}
      </span>
    </span>
  );
}
