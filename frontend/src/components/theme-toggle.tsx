import { Moon, Sun } from "lucide-react";
import { CollapsingText } from "@/components/ui/collapsing-text";
import { useTheme } from "@/lib/theme-context";

/**
 * 侧栏底部主题切换胶囊:暗↔亮循环。
 * 折叠态靠 padding 把图标推到 48px 容器正中(p-2 内宽 48、图标 14):
 *   (48 - 14) / 2 = 17px —— 不用 justify-content:它不可过渡,会瞬跳。
 */
export function ThemeToggle({ collapsed = false }: { collapsed?: boolean }) {
  const { theme, setTheme } = useTheme();
  const isDark = theme === "dark";
  return (
    <button
      type="button"
      onClick={() => setTheme(isDark ? "light" : "dark")}
      aria-pressed={isDark}
      aria-label={isDark ? "切换到浅色主题" : "切换到深色主题"}
      className={`flex w-full items-center rounded-full bg-primary-accent/12 py-1.5 text-xs font-medium text-primary-accent transition-[background-color,padding] duration-(--motion-base) hover:bg-primary-accent/20 ${
        collapsed ? "px-[17px]" : "px-3"
      }`}
    >
      {isDark ? <Sun className="size-3.5 shrink-0" /> : <Moon className="size-3.5 shrink-0" />}
      <CollapsingText collapsed={collapsed} gap="ml-2">
        {isDark ? "切换浅色" : "切换深色"}
      </CollapsingText>
    </button>
  );
}
