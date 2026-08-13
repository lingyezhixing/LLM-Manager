import { type ReactNode } from "react";

/** 日志面板外壳:统一容器 + 头部栏 + 主体。日志查看页(LogViewer)与模型日志面板
 * (ModelLogPanel)共用同一容器/头部样式,仅头部内容与主体(LogLines 或空态)不同。 */
export function LogPane({ header, children }: { header: ReactNode; children: ReactNode }) {
  return (
    <div className="flex h-full flex-col overflow-hidden rounded-lg border border-border bg-card">
      <div className="flex items-center justify-between border-b border-border px-3.5 py-2">
        {header}
      </div>
      {children}
    </div>
  );
}
