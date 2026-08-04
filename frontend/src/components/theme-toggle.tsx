import { Moon, Sun } from "lucide-react";
import { useTheme } from "@/lib/theme-context";

/** 侧栏底部主题切换胶囊(NapCat flat pill 演绎):暗↔亮循环。二态,warm 已退役。 */
export function ThemeToggle({ collapsed = false }: { collapsed?: boolean }) {
  const { theme, setTheme } = useTheme();
  const isDark = theme === "dark";
  return (
    <button
      type="button"
      onClick={() => setTheme(isDark ? "light" : "dark")}
      aria-label={isDark ? "切换到浅色主题" : "切换到深色主题"}
      className={`flex w-full items-center rounded-full bg-primary-accent/12 px-3 py-1.5 text-xs font-medium text-primary-accent transition-[background-color,padding] hover:bg-primary-accent/20 ${
        collapsed ? "justify-center px-0" : ""
      }`}
    >
      {isDark ? <Sun className="size-3.5 shrink-0" /> : <Moon className="size-3.5 shrink-0" />}
      {/* 同 sidebar:文字 grid-cols 收缩过渡,条件渲染会造成瞬间消失闪一下 */}
      <span
        className={`grid transition-[grid-template-columns] duration-200 ${
          collapsed ? "grid-cols-[0fr]" : "ml-2 grid-cols-[1fr]"
        }`}
      >
        <span className="min-w-0 overflow-hidden whitespace-nowrap">
          {isDark ? "切换浅色" : "切换深色"}
        </span>
      </span>
    </button>
  );
}
