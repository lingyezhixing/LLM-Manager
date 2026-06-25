import { useState } from "react";
import { Outlet } from "react-router-dom";
import { Sidebar } from "@/components/sidebar";
import { TopBar } from "@/components/top-bar";

const COLLAPSE_KEY = "lhm:nav-collapsed";

/**
 * App shell: full-width TopBar over a [Sidebar | main <Outlet/>] row.
 * Owns the sidebar collapse state, persisted in localStorage, shared with TopBar + Sidebar.
 */
function AppLayout() {
  const [collapsed, setCollapsed] = useState<boolean>(
    () => localStorage.getItem(COLLAPSE_KEY) === "1",
  );
  const toggle = () => {
    setCollapsed((prev) => {
      const next = !prev;
      localStorage.setItem(COLLAPSE_KEY, next ? "1" : "0");
      return next;
    });
  };
  return (
    <div className="flex h-screen flex-col bg-background text-foreground">
      <TopBar collapsed={collapsed} onToggleCollapse={toggle} />
      <div className="flex flex-1 overflow-hidden">
        <Sidebar collapsed={collapsed} />
        <main className="flex-1 overflow-auto p-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
}

export default AppLayout;
