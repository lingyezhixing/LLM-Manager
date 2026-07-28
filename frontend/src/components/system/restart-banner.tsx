import { Button } from "@/components/ui/button";

// needs_restart 横幅。onRestart 触发自重启(P1c),restarting 反映重连中态。
export function RestartBanner({
  restartFields, serving, onDismiss, onRestart, restarting,
}: {
  restartFields: string[];
  serving: string[];
  onDismiss: () => void;
  onRestart: () => void;
  restarting?: boolean;
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
      <div className="mt-2 flex gap-2">
        <Button size="sm" onClick={onRestart} disabled={restarting}>
          {restarting ? "重启中…" : "立即重启"}
        </Button>
        <Button size="sm" variant="ghost" onClick={onDismiss} disabled={restarting}>
          知道了
        </Button>
      </div>
    </div>
  );
}
