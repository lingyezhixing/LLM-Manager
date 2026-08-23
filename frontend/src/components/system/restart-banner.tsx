import { Button } from "@/components/ui/button";

// 遗留「未生效程序配置」状态条:新保存流(预检→确认→落库)下不应出现;此处兜底
// 旧版本留下的「保存了但没重启」状态。只读展示 + 两个动作(重启生效 / 恢复运行值),
// 无「知道了」(不允许静默丢弃:要么生效、要么回退到运行中的配置)。
// onRestore 使用 running_program 值回写(与当前运行实例一致,不触发重启)。
export function RestartBanner({
  restartFields, serving, onRestore, onRestart, restarting, restoring,
}: {
  restartFields: string[];
  serving: string[];
  onRestore: () => void;
  onRestart: () => void;
  restarting?: boolean;
  restoring?: boolean;
}) {
  return (
    <div className="mb-4 rounded-lg border border-warning/40 bg-warning/10 p-3 text-sm text-foreground">
      <div>
        检测到已保存但未生效的程序配置：
        <strong className="font-medium">{restartFields.join(", ")}</strong>。
      </div>
      {serving.length > 0 && (
        <div className="mt-1 text-muted-foreground">
          当前正在服务的模型:{serving.join(", ")}（重启将中断）。
        </div>
      )}
      <div className="mt-2 flex gap-2">
        <Button size="sm" onClick={onRestart} disabled={restarting || restoring}>
          {restarting ? "重启中…" : "重启程序生效"}
        </Button>
        <Button size="sm" variant="ghost" onClick={onRestore} disabled={restarting || restoring}>
          {restoring ? "恢复中…" : "恢复运行中配置"}
        </Button>
      </div>
    </div>
  );
}
