import { useLocation } from "react-router-dom";
import { PanelLeftClose, PanelLeftOpen } from "lucide-react";
import { NAV_ITEMS } from "@/lib/nav";
import { useHealth } from "@/lib/hooks/use-health";

interface PillBarProps {
  collapsed: boolean;
  onToggleCollapse: () => void;
}

/** 悬浮胶囊条:壳层玻璃第二处。全宽(留边距)。 */
export function PillBar({ collapsed, onToggleCollapse }: PillBarProps) {
  const online = useHealth();
  const { pathname } = useLocation();
  const title = NAV_ITEMS.find((n) => n.path === pathname)?.label ?? "LLM-Manager";
  const ToggleIcon = collapsed ? PanelLeftOpen : PanelLeftClose;
  return (
    <header className="sticky top-4 z-30 mx-2 flex h-10 items-center gap-2 rounded-full border border-border-subtle bg-pill px-2.5 shadow-card backdrop-blur-lg md:mx-3">
      <button
        type="button"
        onClick={onToggleCollapse}
        aria-label={collapsed ? "展开侧栏" : "收起侧栏"}
        aria-expanded={!collapsed}
        className="rounded-full p-1.5 text-muted-foreground transition-colors duration-(--motion-base) hover:bg-card-hover hover:text-foreground"
      >
        <ToggleIcon className="size-4" />
      </button>
      <span className="text-sm font-semibold text-foreground">{title}</span>
      <div className="ml-auto flex items-center gap-2 pr-1.5">
        <span
          title={online ? "后端已连接" : "后端连接中断"}
          className={`inline-flex size-3.5 items-center justify-center rounded-[2px] border-2 ${
            online ? "border-success-accent" : "border-destructive-accent"
          }`}
        >
          {online && <span className="size-1.5 rounded-[1px] bg-success-accent" />}
        </span>
        <span className="hidden text-xs text-muted-foreground sm:inline">
          {online ? "后端已连接" : "后端连接中断"}
        </span>
      </div>
    </header>
  );
}
