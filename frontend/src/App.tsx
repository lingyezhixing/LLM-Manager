import { Navigate, Route, Routes } from "react-router-dom";
import AppLayout from "@/components/app-shell";
import { ConfirmProvider } from "@/components/ui/dialog";
import { ToastProvider } from "@/components/ui/toast";
import LogsPage from "@/pages/logs";
import ModelsPage from "@/pages/models";
import OverviewPage from "@/pages/overview";
import SystemPage from "@/pages/system";
import ToolsPage from "@/pages/tools";
import UsagePage from "@/pages/usage";
import { useUpdateStatus } from "@/lib/hooks/use-config";

// 程序启动时自动检测一次更新(useUpdateStatus 挂载即取;staleTime:Infinity 保证
// 此后无任何自动检测,仅系统页手动点「检查更新」)。不渲染 UI。
function StartupUpdateCheck() {
  useUpdateStatus();
  return null;
}

/** Route tree: AppLayout (shell) wraps all 6 pages via <Outlet/>. Order = locked IA. */
export default function App() {
  return (
    <ToastProvider>
      <ConfirmProvider>
        <StartupUpdateCheck />
        <Routes>
          <Route element={<AppLayout />}>
            <Route index element={<OverviewPage />} />
            <Route path="models" element={<ModelsPage />} />
            <Route path="usage" element={<UsagePage />} />
            <Route path="tools" element={<ToolsPage />} />
            <Route path="logs" element={<LogsPage />} />
            <Route path="system" element={<SystemPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Routes>
      </ConfirmProvider>
    </ToastProvider>
  );
}
