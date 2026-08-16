import { NavLink } from "react-router-dom";
import { NAV_ITEMS } from "@/lib/nav";
import { ThemeToggle } from "@/components/theme-toggle";

interface SidebarProps {
  collapsed: boolean;
}

/**
 * Left navigation sidebar (NapCat 克制演绎). Expanded w-(--container-sidebar) / collapsed w-16 icon rail;
 * glass surface (blur 只在壳层). Active item: primary-accent 12% bg + accent text +
 * right dot. Bottom: theme toggle pill.
 *
 * 折叠动画统一 200ms,三条铁律:① 文字不条件渲染,grid-template-columns 1fr→0fr 收缩 +
 * overflow-hidden 裁剪(条件渲染 = 宽度过渡前文字瞬间消失);② 禁用 justify-content 切换
 * (立即生效不可过渡,图标瞬跳中间再被收缩拉回),居中一律用 padding 过渡(calc 精确值);
 * ③ gap 不可残留(0 宽元素仍占 gap),间距用 margin 且折叠态显式归零。
 */
export function Sidebar({ collapsed }: SidebarProps) {
  return (
    <aside
      className={`${collapsed ? "w-16" : "w-(--container-sidebar)"} flex shrink-0 flex-col border-r border-border bg-sidebar backdrop-blur-xl transition-[width] duration-(--motion-base)`}
    >
      <div
        className={`flex items-center px-4 pb-6 pt-5 transition-[padding] duration-(--motion-base) ${
          // 折叠态左右 padding 居中。三禁令:① justify-content 切换立即生效不可过渡
          // (图标瞬跳);② calc 百分比在过渡中按当前容器宽实时解析(产生驼峰峰值);
          // ③ 圆点尺寸变更时同步此值。公式:(aside 4rem - 圆点 0.75rem)/2 = 1.625rem。
          collapsed ? "px-[1.625rem]" : ""
        }`}
      >
        {/* logo accent bar:展开=竖条,折叠=居中圆点(视觉锚点,呼应导航 active dot) */}
        <span
          className={`shrink-0 rounded-full bg-primary-accent transition-[width,height,border-radius] duration-(--motion-base) ${
            collapsed ? "size-3" : "h-5 w-1"
          }`}
        />
        <span
          className={`grid transition-[grid-template-columns] duration-(--motion-base) ${
            collapsed ? "grid-cols-[0fr]" : "ml-2.5 grid-cols-[1fr]"
          }`}
        >
          <span
            className={`min-w-0 overflow-hidden whitespace-nowrap text-xl font-bold tracking-wide text-foreground transition-opacity duration-(--motion-base) ${
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
                "flex items-center rounded-md px-3 py-2 text-sm transition-[background-color,color,transform,padding] duration-150",
                // 折叠态 padding 居中,同 header 三禁令。公式:(aside 4rem - nav px-2 两侧 1rem - icon 0.875rem)/2
                collapsed && "px-[1.0625rem]",
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
                  className={`grid transition-[grid-template-columns] duration-(--motion-base) ${
                    collapsed ? "grid-cols-[0fr]" : "ml-3 min-w-0 flex-1 grid-cols-[1fr]"
                  }`}
                >
                  <span className="min-w-0 truncate">{label}</span>
                </span>
                {isActive && (
                  <span
                    className={`size-1.5 shrink-0 rounded-full bg-primary-accent transition-[width,margin,opacity] duration-(--motion-base) ${
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
