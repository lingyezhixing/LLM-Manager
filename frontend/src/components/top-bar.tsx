import { PanelLeftClose, PanelLeftOpen } from "lucide-react";
import { ThemeSwitcher } from "@/components/theme-switcher";

interface TopBarProps {
  collapsed: boolean;
  onToggleCollapse: () => void;
}

/**
 * Full-width top bar. Left cluster = logo + collapse toggle (paired app-chrome unit).
 * Right = theme switcher. Deliberately minimal (no status chip, no stop-all).
 */
export function TopBar({ collapsed, onToggleCollapse }: TopBarProps) {
  const ToggleIcon = collapsed ? PanelLeftOpen : PanelLeftClose;
  return (
    <header className="flex h-14 shrink-0 items-center justify-between border-b border-border bg-card px-4">
      <div className="flex items-center gap-1">
        <span className="px-1 font-semibold tracking-tight">▣ LLM-Manager</span>
        <button
          type="button"
          onClick={onToggleCollapse}
          aria-label={collapsed ? "展开侧栏" : "收起侧栏"}
          className="rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
        >
          <ToggleIcon className="size-4" />
        </button>
      </div>
      <ThemeSwitcher />
    </header>
  );
}
