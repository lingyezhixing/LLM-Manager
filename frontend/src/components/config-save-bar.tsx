import { Button } from "@/components/ui/button";

// 通用面板底部 dirty 态保存栏。父级常驻渲染,用 open 控制展开/收起——
// 条件渲染会让表单在"刚变脏"和"刚保存完"两个瞬间被顶一下,这里改成
// grid-rows 0fr↔1fr + 透明度过渡(与侧栏折叠同一套做法,纯 CSS、无退出卸载)。
// saveDisabled:客户端门控(如必填校验未过)时禁用保存按钮;默认 false(通用面板不传 → 不变)。
export function ConfigSaveBar({
  open,
  saving,
  error,
  onSave,
  onReset,
  saveDisabled,
}: {
  open: boolean;
  saving: boolean;
  error?: string | null;
  onSave: () => void;
  onReset: () => void;
  saveDisabled?: boolean;
}) {
  return (
    <div
      aria-hidden={!open}
      inert={!open}
      className={`grid transition-[grid-template-rows,opacity] duration-(--motion-base) ease-(--motion-ease) ${
        open ? "grid-rows-[1fr] opacity-100" : "grid-rows-[0fr] opacity-0"
      }`}
    >
      <div className="min-h-0 overflow-hidden">
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
      </div>
    </div>
  );
}
