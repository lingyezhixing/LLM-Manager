import { Button } from "@/components/ui/button";

// 通用面板底部 dirty 态保存栏。父级仅在 dirty 时渲染。
// saveDisabled:客户端门控(如必填校验未过)时禁用保存按钮;默认 false(通用面板不传 → 不变)。
export function ConfigSaveBar({
  saving,
  error,
  onSave,
  onReset,
  saveDisabled,
}: {
  saving: boolean;
  error?: string | null;
  onSave: () => void;
  onReset: () => void;
  saveDisabled?: boolean;
}) {
  return (
    <div className="mt-2 flex flex-col items-end gap-2">
      <div className="flex items-center gap-3">
        <Button size="sm" onClick={onSave} disabled={saving || saveDisabled}>
          {saving ? "保存中…" : "保存"}
        </Button>
        <Button size="sm" variant="ghost" onClick={onReset} disabled={saving}>
          重置
        </Button>
      </div>
      {error && <span className="text-xs text-destructive">{error}</span>}
    </div>
  );
}
