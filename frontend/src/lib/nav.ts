import type { LucideIcon } from "lucide-react";
import { BarChart3, Boxes, LayoutDashboard, ScrollText, Settings, Wrench } from "lucide-react";

interface NavItem {
  /** 展开侧边栏中显示的完整标签。 */
  label: string;
  /** 路由路径。 */
  path: string;
  /** 单色线框图标(lucide)— 内联打包,离线安全。 */
  icon: LucideIcon;
}

/**
 * 侧边栏导航的单一事实源。
 */
export const NAV_ITEMS: readonly NavItem[] = [
  { label: "概览", path: "/", icon: LayoutDashboard },
  { label: "模型管理", path: "/models", icon: Boxes },
  { label: "用量统计", path: "/usage", icon: BarChart3 },
  { label: "日志查看", path: "/logs", icon: ScrollText },
  { label: "工具箱", path: "/tools", icon: Wrench },
  { label: "系统", path: "/system", icon: Settings },
] as const;
