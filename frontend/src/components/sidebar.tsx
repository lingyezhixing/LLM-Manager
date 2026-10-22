import { NavLink } from "react-router-dom";
import { NAV_ITEMS } from "@/lib/nav";
import { ThemeToggle } from "@/components/theme-toggle";
import { CollapsingText } from "@/components/ui/collapsing-text";

interface SidebarProps {
  collapsed: boolean;
}

/**
 * 折叠态把内容推到轨道正中。不能用 justify-content:该属性立即生效、不可过渡,
 * 图标会先瞬跳到中间再被收缩拉回;只能用 padding 过渡。
 *   brand:(64 - 17.5) / 2 = 23.25 → 23px(差 0.25px,肉眼不可见)
 *   nav:  (64 - 导航容器 px-2 两侧 16 - 图标 16) / 2 = 16px
 * 注意:标记/图标尺寸变更时,这两个值要跟着重算。
 */
const RAIL_PAD = { brand: "px-[23px]", nav: "px-4" } as const;

/** logo 的 L 折面:竖笔从右下 68% 斜切到左下角,横笔从 44% 斜到 0,两条 45° 线重合。
 *  比例取自 favicon(竖笔 70 / 总高 220 / 横笔高 70);折叠态 24px 高即 7.6 / 17.5 / 7.6。 */
const CLIP_STEM_FLAT = "polygon(0 0, 100% 0, 100% 100%, 0 100%)";
const CLIP_STEM_FOLD = "polygon(0 0, 100% 0, 100% 68%, 0 100%)";
const CLIP_FOOT = "polygon(44% 0, 100% 0, 100% 100%, 0 100%)";

/**
 * 品牌标记。展开时就是那根主色竖条;折叠过程中竖条长成 logo 的 L ——
 * 变粗变高、主色转前景色(深色→白 / 浅色→黑),底部长出蓝色横笔,
 * 交界按 logo 的折面切成 45°。三层尺寸同步过渡,读起来是"条展开成 L"。
 */
function BrandMark({ collapsed }: { collapsed: boolean }) {
  return (
    <span
      aria-hidden
      className={`relative block shrink-0 transition-[width,height] duration-(--motion-base) ${
        collapsed ? "h-6 w-[17.5px]" : "h-5 w-1"
      }`}
    >
      <span
        className={`absolute bottom-0 left-0 bg-primary-accent transition-[width,height] duration-(--motion-base) ${
          collapsed ? "h-[7.6px] w-[17.5px]" : "h-0 w-1"
        }`}
        style={{ clipPath: CLIP_FOOT }}
      />
      <span
        className={`absolute bottom-0 left-0 transition-[width,height,background-color,clip-path] duration-(--motion-base) ${
          collapsed ? "h-6 w-[7.6px] bg-foreground" : "h-5 w-1 rounded-full bg-primary-accent"
        }`}
        style={{ clipPath: collapsed ? CLIP_STEM_FOLD : CLIP_STEM_FLAT }}
      />
    </span>
  );
}

function navItemClass(collapsed: boolean, isActive: boolean): string {
  return [
    // 折叠/展开用互斥的 padding 类(不要 px-3 + px-4 叠加:两者都在样式表里,谁生效取决于产出顺序)
    "flex items-center rounded-md py-2 text-sm transition-[background-color,color,transform,padding] duration-(--motion-base)",
    collapsed ? RAIL_PAD.nav : "px-3",
    isActive
      ? "bg-primary-accent/12 font-medium text-primary-accent"
      : "text-muted-foreground hover:translate-x-[3px] hover:bg-card-hover hover:text-foreground",
  ].join(" ");
}

/** 激活圆点:折叠时宽度归零 + 淡出(仍挂载,展开时才能过渡回来)。 */
function activeDotClass(collapsed: boolean): string {
  return `shrink-0 rounded-full bg-primary-accent transition-[width,opacity] duration-(--motion-base) ${
    collapsed ? "h-1.5 w-0 opacity-0" : "ml-auto h-1.5 w-1.5 opacity-100"
  }`;
}

/**
 * 左侧导航:展开 = 图标 + 文字,折叠 = 64px 图标轨;激活项为主色 12% 底 + accent 文字 + 右侧圆点。
 * 玻璃表面只在壳层(侧栏 + 胶囊条),内容卡片不用 blur。
 */
export function Sidebar({ collapsed }: SidebarProps) {
  return (
    <aside
      className={`${collapsed ? "w-16" : "w-(--container-sidebar)"} flex shrink-0 flex-col border-r border-border bg-sidebar backdrop-blur-xl transition-[width] duration-(--motion-base)`}
    >
      <div
        className={`flex items-center pb-6 pt-5 transition-[padding] duration-(--motion-base) ${
          collapsed ? RAIL_PAD.brand : "px-4"
        }`}
      >
        <BrandMark collapsed={collapsed} />
        <CollapsingText
          collapsed={collapsed}
          gap="ml-2.5"
          className="text-xl font-bold tracking-wide text-foreground"
        >
          LLM-Manager
        </CollapsingText>
      </div>

      <nav className="flex flex-col gap-1.5 px-2">
        {NAV_ITEMS.map(({ label, path, icon: Icon }) => (
          <NavLink
            key={path}
            to={path}
            end={path === "/"}
            title={collapsed ? label : undefined}
            className={({ isActive }) => navItemClass(collapsed, isActive)}
          >
            {({ isActive }) => (
              <>
                <Icon className="size-4 shrink-0" />
                <CollapsingText collapsed={collapsed} gap="ml-3 flex-1" className="truncate">
                  {label}
                </CollapsingText>
                {isActive && <span className={activeDotClass(collapsed)} />}
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
