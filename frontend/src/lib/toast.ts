import { createContext } from "react";

// Toast 命令式 API 的 context 载体(与组件同文件会触发 react-refresh only-export-components,
// 故独立:toast.tsx 渲染层 / use-toast.ts 消费 hook / 本文件共享类型 + context)。
export interface ToastApi {
  success: (message: string) => void;
  error: (message: string) => void;
  info: (message: string) => void;
}

export const ToastContext = createContext<ToastApi | null>(null);
