import { useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { useConfirm } from "@/components/ui/dialog";
import { ErrorState } from "@/components/ui/error-state";
import { useToast } from "@/components/ui/toast";
import { ModelDefForm } from "@/components/system/model-def-form";
import { useDeleteModelDef, useModelDef, useModelDefs, useRestartModel } from "@/lib/hooks/use-model-defs";
import type { ModelWriteResult } from "@/lib/api";

// 模型定义 CRUD 面板:顶部选择带 + 下方详情(新建/编辑/删除)+ 编辑后按模型重启提示。
// selected:undefined=未选(默认第一个);null=创建态;string=已选模型。
export function ModelDefPanel() {
  const list = useModelDefs();
  const [selected, setSelected] = useState<string | null | undefined>(undefined);
  const [createNonce, setCreateNonce] = useState(0);
  const [hint, setHint] = useState<{ name: string; served: string } | null>(null);
  const dirtyRef = useRef(false);
  const confirm = useConfirm();
  const toast = useToast();

  const items = [...(list.data ?? [])].sort((a, b) => a.port - b.port);
  const effSelected = selected === undefined ? (items[0]?.name ?? null) : selected;
  const detail = useModelDef(effSelected);
  const del = useDeleteModelDef();
  const restart = useRestartModel();

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
    setHint(null);
    setSelected(name);
  };
  const startCreate = async () => {
    if (selected === null) return;
    if (!(await guard())) return;
    setHint(null);
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
        setHint(null);
        setSelected(undefined);
        dirtyRef.current = false;
        toast.success(`已删除模型「${name}」`);
      },
    });
  };
  const onSaved = (result: ModelWriteResult, name: string) => {
    dirtyRef.current = false;
    const wasCreate = selected === null;
    if (result.hint === "restart_model" && result.affected_routing.length > 0) {
      setHint({ name, served: result.affected_routing[0] });
    } else {
      setHint(null);
    }
    setSelected(name);   // 新建/改名 → 切到该名;普通保存 name=当前选中,setSelected 无副作用
    toast.success(wasCreate ? `已创建模型「${name}」` : "已保存");
  };

  const formKey = typeof effSelected === "string" ? effSelected : `new-${createNonce}`;

  // 详情区:列表加载中 / 编辑态详情加载中 → 加载提示;创建态 → 空表单;否则表单。
  let formArea;
  if (list.isLoading) {
    formArea = <div className="text-sm text-muted-foreground">加载中…</div>;
  } else if (list.isError) {
    formArea = <ErrorState message={(list.error as Error).message} onRetry={() => list.refetch()} />;
  } else if (typeof effSelected === "string") {
    formArea =
      detail.isLoading || !detail.data ? (
        <div className="text-sm text-muted-foreground">加载中…</div>
      ) : (
        <ModelDefForm
          key={formKey}
          model={detail.data}
          onSaved={onSaved}
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

  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,280px)_minmax(0,1fr)]">
      {/* 左栏:模型列表(按 port 升序)+ 底部操作(新增 / 删除)*/}
      <div className="flex flex-col gap-1">
        {list.isLoading && (
          <span className="px-3 py-2 text-sm text-muted-foreground">加载中…</span>
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
                  "rounded-md px-3 py-2 text-left text-sm transition-colors " +
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
          {typeof effSelected === "string" && (
            <Button
              type="button"
              size="sm"
              variant="ghost"
              onClick={onDelete}
              className="w-full justify-start text-destructive"
              disabled={del.isPending}
            >
              删除模型
            </Button>
          )}
          {del.error && (
            <div className="px-3 text-xs text-destructive">
              删除失败:{(del.error as Error).message}
            </div>
          )}
        </div>
      </div>

      {/* 右栏:重启提示 + 详情表单 */}
      <div>
        {hint && (
          <div className="mb-4 flex flex-wrap items-center gap-3 rounded-md border border-warning/40 bg-warning/10 px-3 py-2 text-sm">
            <span className="text-foreground">
              配置已保存 · 运行中实例(<span className="font-medium">{hint.served}</span>)需重启生效
            </span>
            <Button
              size="sm"
              onClick={() =>
                restart.mutate(hint.served, {
                  onSuccess: () => {
                    setHint(null);
                    toast.success(`已重启 ${hint.served}`);
                  },
                  onError: (e: unknown) => toast.error((e as Error).message),
                })
              }
              disabled={restart.isPending}
            >
              {restart.isPending ? "重启中…" : "重启"}
            </Button>
            <button
              type="button"
              className="text-xs text-muted-foreground hover:text-foreground"
              onClick={() => setHint(null)}
            >
              忽略
            </button>
          </div>
        )}

        {formArea}
      </div>
    </div>
  );
}
