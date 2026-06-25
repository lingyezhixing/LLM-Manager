import { NavLink } from "react-router-dom";
import { NAV_ITEMS } from "@/lib/nav";

interface SidebarProps {
  collapsed: boolean;
}

/**
 * Left navigation sidebar. Expanded = icon + label (~w-52); collapsed = icon rail (~w-14).
 * Active item: muted bg + primary left accent bar + foreground text.
 */
export function Sidebar({ collapsed }: SidebarProps) {
  return (
    <aside
      className={`${collapsed ? "w-14" : "w-52"} shrink-0 border-r border-border bg-card transition-[width] duration-150`}
    >
      <nav className="flex flex-col gap-0.5 p-2">
        {NAV_ITEMS.map(({ label, path, icon: Icon }) => (
          <NavLink
            key={path}
            to={path}
            end={path === "/"}
            title={collapsed ? label : undefined}
            className={({ isActive }) =>
              [
                "flex items-center gap-3 rounded-md border-l-2 px-3 py-2 text-sm transition-colors",
                collapsed && "justify-center px-0",
                isActive
                  ? "border-primary bg-muted font-medium text-foreground"
                  : "border-transparent text-muted-foreground hover:bg-muted hover:text-foreground",
              ]
                .filter(Boolean)
                .join(" ")
            }
          >
            <Icon className="size-4 shrink-0" />
            {!collapsed && <span className="truncate">{label}</span>}
          </NavLink>
        ))}
      </nav>
    </aside>
  );
}
