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

// 启动时预读自更新状态缓存(后端已在程序启动时后台检测一次,存于 /api/update/status;
// 此处只是 GET 读缓存预取,不触发任何检测)。不渲染 UI。
function StartupUpdateCheck() {
  useUpdateStatus();
  return null;
}

/** 路由树:AppLayout(壳层)经 <Outlet/> 包裹全部 6 个页面。顺序 = 锁定 IA。 */
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
