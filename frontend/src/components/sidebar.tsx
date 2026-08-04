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
 *
 * 折叠动画统一 200ms:文字不条件渲染,改用 grid-template-columns 1fr→0fr 收缩 + overflow-hidden
 * 裁剪(opacity 同步淡出)。若用 {!collapsed && <span>} 会在宽度过渡前瞬间移除文字、瞬间跳中
 * 图标,造成"先闪一下再收窄"。gap 同理不可残留(折叠时 0 宽元素仍占 gap),间距一律用 margin。
 */
export function Sidebar({ collapsed }: SidebarProps) {
  return (
    <aside
      className={`${collapsed ? "w-16" : "w-64"} flex shrink-0 flex-col border-r border-border bg-sidebar backdrop-blur-xl transition-[width] duration-200`}
    >
      <div
        className={`flex items-center px-4 pb-6 pt-5 transition-[padding] duration-200 ${
          collapsed ? "justify-center px-0" : ""
        }`}
      >
        {/* logo accent bar:展开=竖条,折叠=居中圆点(视觉锚点,呼应导航 active dot) */}
        <span
          className={`shrink-0 rounded-full bg-primary-accent transition-[width,height,border-radius] duration-200 ${
            collapsed ? "size-2" : "h-5 w-1"
          }`}
        />
        <span
          className={`grid transition-[grid-template-columns] duration-200 ${
            collapsed ? "grid-cols-[0fr]" : "ml-2.5 grid-cols-[1fr]"
          }`}
        >
          <span
            className={`min-w-0 overflow-hidden whitespace-nowrap text-xl font-bold tracking-wide text-foreground transition-opacity duration-200 ${
              collapsed ? "opacity-0" : "opacity-100"
            }`}
          >
            LLM-Manager
          </span>
        </span>
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
                "flex items-center rounded-md px-3 py-2 text-sm transition-[background-color,color,transform] duration-150",
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
                <span
                  className={`grid transition-[grid-template-columns] duration-200 ${
                    collapsed ? "grid-cols-[0fr]" : "ml-3 min-w-0 flex-1 grid-cols-[1fr]"
                  }`}
                >
                  <span className="min-w-0 truncate">{label}</span>
                </span>
                {isActive && (
                  <span
                    className={`size-1.5 shrink-0 rounded-full bg-primary-accent transition-[width,margin,opacity] duration-200 ${
                      collapsed ? "w-0 opacity-0" : "ml-auto opacity-100"
                    }`}
                  />
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
