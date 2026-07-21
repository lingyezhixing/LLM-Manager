// needs_restart 非阻塞横幅(L2:改 spec 的模态为横幅)。onRestart 预留给 P1c 自重启。
export function RestartBanner({
  restartFields, serving, onDismiss, onRestart,
}: {
  restartFields: string[];
  serving: string[];
  onDismiss: () => void;
  onRestart?: () => void;
}) {
  return (
    <div className="mb-4 rounded-lg border border-warning/40 bg-warning/10 p-3 text-sm text-foreground">
      <div>
        已保存。以下变更需重启程序生效：
        <strong className="font-medium">{restartFields.join(", ")}</strong>。
      </div>
      {serving.length > 0 && (
        <div className="mt-1 text-muted-foreground">
          当前正在服务的模型：{serving.join(", ")}（重启将中断）。
        </div>
      )}
      <div className="mt-2 flex gap-3">
        {onRestart && (
          <button type="button" onClick={onRestart} className="text-xs underline text-primary">
            立即重启
          </button>
        )}
        <button type="button" onClick={onDismiss} className="text-xs underline text-muted-foreground">
          知道了
        </button>
      </div>
    </div>
  );
}
