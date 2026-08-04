import { useState } from "react";
import { Outlet, useLocation } from "react-router-dom";
import { PillBar } from "@/components/pill-bar";
import { Sidebar } from "@/components/sidebar";

const COLLAPSE_KEY = "lhm:nav-collapsed";

/**
 * App shell (NapCat 克制演绎):左玻璃侧栏 + 右滚动列(悬浮胶囊条 + 全宽内容)。
 * 控制台数据密集——内容区占满宽度(弃 NapCat 居中 1000px 列)。
 * 折叠状态 localStorage 持久化,键不变。页面切换经 key=pathname 触发 animate-page-in。
 */
function AppLayout() {
  const [collapsed, setCollapsed] = useState<boolean>(
    () => localStorage.getItem(COLLAPSE_KEY) === "1",
  );
  const { pathname } = useLocation();
  const toggle = () => {
    setCollapsed((prev) => {
      const next = !prev;
      localStorage.setItem(COLLAPSE_KEY, next ? "1" : "0");
      return next;
    });
  };
  return (
    <div className="flex h-screen overflow-hidden bg-background text-foreground">
      <Sidebar collapsed={collapsed} />
      <div className="flex flex-1 flex-col overflow-y-auto">
        <PillBar collapsed={collapsed} onToggleCollapse={toggle} />
        <main
          key={pathname}
          className="animate-page-in w-full flex-1 px-4 py-4 md:px-6"
        >
          <Outlet />
        </main>
      </div>
    </div>
  );
}

export default AppLayout;
