import type { LucideIcon } from "lucide-react";
import { BarChart3, Boxes, LayoutDashboard, ScrollText, Settings, Wrench } from "lucide-react";

interface NavItem {
  /** Full label shown in the expanded sidebar (讨论简写的扩充版). */
  label: string;
  /** Route path. */
  path: string;
  /** Monochrome line icon (lucide) — bundled inline, offline-safe. */
  icon: LucideIcon;
}

/**
 * Single source of truth for sidebar navigation.
 * Order: 概览 / 模型管理 / 用量统计 / 日志查看 / 工具箱 / 系统.
 */
export const NAV_ITEMS: readonly NavItem[] = [
  { label: "概览", path: "/", icon: LayoutDashboard },
  { label: "模型管理", path: "/models", icon: Boxes },
  { label: "用量统计", path: "/usage", icon: BarChart3 },
  { label: "日志查看", path: "/logs", icon: ScrollText },
  { label: "工具箱", path: "/tools", icon: Wrench },
  { label: "系统", path: "/system", icon: Settings },
] as const;
