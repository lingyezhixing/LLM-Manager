// Button 变体类名单源:Button 原语与 buttonClasses(供 Link 等非 button 元素复用
// Button 视觉,如空态引导跳转)共用,防悬停/尺寸语言分叉。独立于 button.tsx 是为
// 满足 fast-refresh 的 only-export-components 约束(组件文件只导出组件)。

export type ButtonVariant = "default" | "ghost" | "destructive" | "outline";
export type ButtonSize = "sm" | "md";

const VARIANTS: Record<ButtonVariant, string> = {
  default: "bg-primary text-primary-foreground hover:bg-primary-600",
  ghost: "bg-transparent text-muted-foreground hover:bg-card-hover hover:text-foreground",
  destructive: "bg-destructive text-destructive-foreground hover:bg-destructive-600",
  outline: "border border-border bg-card text-foreground hover:bg-card-hover",
};
const SIZES: Record<ButtonSize, string> = { sm: "h-8 px-3 text-xs", md: "h-9 px-4 text-sm" };

const BASE =
  "inline-flex items-center justify-center rounded-md font-medium transition-[color,background-color,border-color,opacity,transform,box-shadow] duration-(--motion-fast) active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-40 disabled:saturate-0 disabled:active:scale-100";

export function buttonClasses(
  variant: ButtonVariant = "default",
  size: ButtonSize = "md",
  className = "",
): string {
  return `${BASE} ${VARIANTS[variant]} ${SIZES[size]} ${className}`;
}
