import { Button } from "@/components/ui/button";

// 通用面板底部 dirty 态保存栏。父级仅在 dirty 时渲染。
export function ConfigSaveBar({
  saving, error, onSave, onReset,
}: {
  saving: boolean;
  error?: string | null;
  onSave: () => void;
  onReset: () => void;
}) {
  return (
    <div className="mt-2 flex items-center gap-3">
      <Button size="sm" onClick={onSave} disabled={saving}>
        {saving ? "保存中…" : "保存"}
      </Button>
      <Button size="sm" variant="ghost" onClick={onReset} disabled={saving}>
        重置
      </Button>
      {error && <span className="text-xs text-destructive">{error}</span>}
    </div>
  );
}
