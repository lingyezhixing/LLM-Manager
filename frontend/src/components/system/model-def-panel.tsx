import { useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Loading } from "@/components/ui/card";
import { useConfirm } from "@/lib/hooks/use-confirm";
import { ErrorState } from "@/components/ui/error-state";
import { byPort, errMsg } from "@/lib/format";
import { useToast } from "@/lib/hooks/use-toast";
import { ModelDefForm } from "@/components/system/model-def-form";
import { useDeleteModelDef, useModelDef, useModelDefs } from "@/lib/hooks/use-model-defs";
import type { ModelWriteResult } from "@/lib/api";

// 模型定义 CRUD 面板:顶部选择带 + 下方详情(新建/编辑/删除)。
// 「保存是否需重启模型」由 ModelDefForm 保存流内预检确认(先检测后落库),panel 只管
// 列表与切换;编辑保存后若涉及重启,form 内链式发起 restart(状态经 SSE 回映)。
// selected:undefined=未选(默认第一个);null=创建态;string=已选模型。
export function ModelDefPanel() {
  const list = useModelDefs();
  const [selected, setSelected] = useState<string | null | undefined>(undefined);
  const [createNonce, setCreateNonce] = useState(0);
  const dirtyRef = useRef(false);
  const confirm = useConfirm();
  const toast = useToast();

  const items = byPort(list.data ?? []);
  const effSelected = selected === undefined ? (items[0]?.name ?? null) : selected;
  const detail = useModelDef(effSelected);
  const del = useDeleteModelDef();

  // 切换前 dirty 守卫(M9):dirty 则确认。
  const guard = async (): Promise<boolean> =>
    !dirtyRef.current
    || await confirm({
      title: "放弃未保存的修改?",
      description: "当前模型有未保存修改,切换将丢弃。",
      confirmText: "放弃",
      cancelText: "继续编辑",
      danger: true,
    });

  const selectModel = async (name: string) => {
    if (name === effSelected) return;
    if (!(await guard())) return;
    setSelected(name);
  };
  const startCreate = async () => {
    if (selected === null) return;
    if (!(await guard())) return;
    setSelected(null);
    setCreateNonce((n) => n + 1);
  };
  const onDelete = async () => {
    if (typeof effSelected !== "string") return;
    const name = effSelected;
    const ok = await confirm({
      title: `删除模型 ${name}?`,
      description: "将同时删除该模型的日志记录;请求记录保留(可在数据库管理页的孤立模型处清理)。",
      confirmText: "删除",
      cancelText: "取消",
      danger: true,
    });
    if (!ok) return;
    del.mutate(name, {
      onSuccess: () => {
        setSelected(undefined);
        dirtyRef.current = false;
        toast.success(`已删除模型「${name}」`);
      },
    });
  };
  const onSaved = (_result: ModelWriteResult, name: string) => {
    dirtyRef.current = false;
    setSelected(name);   // 新建/改名 → 切到该名;普通保存 name=当前选中,setSelected 无副作用
    toast.success("已保存");
  };

  const formKey = typeof effSelected === "string" ? effSelected : `new-${createNonce}`;

  // 详情区:列表加载中 / 编辑态详情加载中 → 加载提示;创建态 → 空表单;否则表单。
  let formArea;
  if (list.isLoading) {
    formArea = <Loading />;
  } else if (list.isError) {
    formArea = <ErrorState message={errMsg(list.error)} onRetry={() => list.refetch()} />;
  } else if (typeof effSelected === "string") {
    formArea =
      detail.isLoading || !detail.data ? (
        <Loading />
      ) : (
        <ModelDefForm
          key={formKey}
          model={detail.data}
          onSaved={onSaved}
          onDelete={onDelete}
          onDirtyChange={(d) => {
            dirtyRef.current = d;
          }}
        />
      );
  } else {
    formArea = (
      <ModelDefForm
        key={formKey}
        model={null}
        onSaved={onSaved}
        onDirtyChange={(d) => {
          dirtyRef.current = d;
        }}
      />
    );
  }

  // 左栏 sticky 独立滚动:页面保持滚动(NavTabs 吸顶玻璃效果继续生效),
  // 左栏吸顶位置 = PillBar 72 + NavTabs ~38 + mt-6 24 = 134px;max-h 让底部留 16px。
  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,280px)_minmax(0,1fr)]">
      {/* 左栏:模型列表(按 port 升序)+ 底部操作(新增 / 删除),吸顶后独立滚动 */}
      <div className="flex flex-col gap-1 lg:sticky lg:top-[134px] lg:max-h-[calc(100dvh-150px)] lg:overflow-y-auto">
        {list.isLoading && (
          <Loading />
        )}
        <div className="flex flex-col gap-0.5" role="listbox" aria-label="模型列表">
          {items.map((m) => {
            const selected = m.name === effSelected;
            return (
              <button
                key={m.name}
                type="button"
                role="option"
                aria-selected={selected}
                onClick={() => selectModel(m.name)}
                className={
                  "rounded-md px-3 py-2 text-left text-sm transition-colors duration-(--motion-base) " +
                  (selected
                    ? "bg-primary-accent/12 font-medium text-primary-accent"
                    : "text-muted-foreground hover:bg-card-hover hover:text-foreground")
                }
              >
                {m.name}
              </button>
            );
          })}
        </div>
        <div className="flex flex-col gap-1 border-t border-border pt-2">
          <Button type="button" size="sm" variant="ghost" onClick={startCreate} className="w-full justify-start">
            + 新增
          </Button>
          {del.error && (
            <div className="px-3 text-xs text-destructive">
              删除失败:{errMsg(del.error)}
            </div>
          )}
        </div>
      </div>

      {/* 右栏:详情表单(删除按钮在表单名称行内),随页面滚动;保存确认流在表单内 */}
      <div>
        {formArea}
      </div>
    </div>
  );
}
