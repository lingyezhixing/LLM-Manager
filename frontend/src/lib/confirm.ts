import { createContext } from "react";

// Confirm 对话框命令式 API 的 context 载体(与组件同文件会触发 react-refresh
// only-export-components,故独立:dialog.tsx 渲染层 / use-confirm.ts 消费 hook)。
export interface ConfirmOptions {
  title: string;
  description?: string;
  confirmText?: string;
  cancelText?: string;
  danger?: boolean;
}

export type ConfirmFn = (opts: ConfirmOptions) => Promise<boolean>;

export const ConfirmContext = createContext<ConfirmFn | null>(null);
