import { Route, Routes } from "react-router-dom";
import AppLayout from "@/components/app-shell";
import { ConfirmProvider } from "@/components/ui/dialog";
import { ToastProvider } from "@/components/ui/toast";
import LogsPage from "@/pages/logs";
import ModelsPage from "@/pages/models";
import OverviewPage from "@/pages/overview";
import SystemPage from "@/pages/system";
import ToolsPage from "@/pages/tools";
import UsagePage from "@/pages/usage";

/** Route tree: AppLayout (shell) wraps all 6 pages via <Outlet/>. Order = locked IA. */
export default function App() {
  return (
    <ToastProvider>
      <ConfirmProvider>
        <Routes>
          <Route element={<AppLayout />}>
            <Route index element={<OverviewPage />} />
            <Route path="models" element={<ModelsPage />} />
            <Route path="usage" element={<UsagePage />} />
            <Route path="tools" element={<ToolsPage />} />
            <Route path="logs" element={<LogsPage />} />
            <Route path="system" element={<SystemPage />} />
            <Route path="*" element={<OverviewPage />} />
          </Route>
        </Routes>
      </ConfirmProvider>
    </ToastProvider>
  );
}
