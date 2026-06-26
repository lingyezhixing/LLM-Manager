import { PanelLeftClose, PanelLeftOpen } from "lucide-react";
import { ThemeSwitcher } from "@/components/theme-switcher";
import { useHealth } from "@/lib/use-health";

interface TopBarProps {
  collapsed: boolean;
  onToggleCollapse: () => void;
}

/**
 * Full-width top bar. Left cluster = logo + collapse toggle (paired app-chrome unit).
 * The logo glyph is a backend-health LED: ▣ (filled, center dot) while /health succeeds,
 * □ (hollow) when the probe fails. Right = theme switcher.
 */
export function TopBar({ collapsed, onToggleCollapse }: TopBarProps) {
  const online = useHealth();
  const ToggleIcon = collapsed ? PanelLeftOpen : PanelLeftClose;
  return (
    <header className="flex h-14 shrink-0 items-center justify-between border-b border-border bg-card px-4">
      <div className="flex items-center gap-1">
        <span
          className="px-1 font-semibold tracking-tight"
          title={online ? "后端已连接" : "后端连接中断"}
        >
          <span className={online ? "text-success" : "text-destructive"}>
            {online ? "▣" : "□"}
          </span>{" "}LLM-Manager
        </span>
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
