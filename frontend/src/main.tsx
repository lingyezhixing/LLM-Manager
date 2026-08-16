import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "./App";
import "./index.css";
import { ThemeProvider } from "@/components/theme-provider";
import { ErrorBoundary } from "@/components/ui/error-boundary";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 15_000, // 本地服务数据新鲜窗口;SSE 驱动页不走 query 不受影响
      retry: 1, // 本地服务,重试一次足够(默认 3 次拖长错误呈现)
      refetchOnWindowFocus: false, // 单人本地工具,无焦点刷新必要
    },
  },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ErrorBoundary>
      <BrowserRouter>
        <QueryClientProvider client={queryClient}>
          <ThemeProvider>
            <App />
          </ThemeProvider>
        </QueryClientProvider>
      </BrowserRouter>
    </ErrorBoundary>
  </StrictMode>,
);
