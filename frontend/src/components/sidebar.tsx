import { NavLink } from "react-router-dom";
import { NAV_ITEMS } from "@/lib/nav";
import { ThemeToggle } from "@/components/theme-toggle";

interface SidebarProps {
  collapsed: boolean;
}

/**
 * Left navigation sidebar (NapCat 克制演绎). Expanded w-64 / collapsed w-16 icon rail;
 * glass surface (blur 只在壳层). Active item: primary-accent 12% bg + accent text +
 * right dot. Bottom: theme toggle pill.
 */
export function Sidebar({ collapsed }: SidebarProps) {
  return (
    <aside
      className={`${collapsed ? "w-16" : "w-64"} flex shrink-0 flex-col border-r border-border bg-sidebar backdrop-blur-xl transition-[width] duration-200`}
    >
      <div
        className={`flex items-center gap-2.5 px-4 pb-6 pt-5 ${
          collapsed ? "justify-center px-0" : ""
        }`}
      >
        <span className="h-5 w-1 rounded-full bg-primary-accent" />
        {!collapsed && (
          <span className="text-xl font-bold tracking-wide text-foreground">LLM-Manager</span>
        )}
      </div>
      <nav className="flex flex-col gap-1.5 px-2">
        {NAV_ITEMS.map(({ label, path, icon: Icon }) => (
          <NavLink
            key={path}
            to={path}
            end={path === "/"}
            title={collapsed ? label : undefined}
            className={({ isActive }) =>
              [
                "flex items-center gap-3 rounded-md px-3 py-2 text-sm transition-[background-color,color,transform] duration-150",
                collapsed && "justify-center px-0",
                isActive
                  ? "bg-primary-accent/12 font-medium text-primary-accent"
                  : "text-muted-foreground hover:translate-x-[3px] hover:bg-card-hover hover:text-foreground",
              ]
                .filter(Boolean)
                .join(" ")
            }
          >
            {({ isActive }) => (
              <>
                <Icon className="size-4 shrink-0" />
                {!collapsed && <span className="truncate">{label}</span>}
                {isActive && !collapsed && (
                  <span className="ml-auto size-1.5 shrink-0 rounded-full bg-primary-accent" />
                )}
              </>
            )}
          </NavLink>
        ))}
      </nav>
      <div className="mt-auto p-2">
        <ThemeToggle collapsed={collapsed} />
      </div>
    </aside>
  );
}
