import { useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Loading } from "@/components/ui/card";
import { ErrorState } from "@/components/ui/error-state";
import { Switch } from "@/components/ui/form";
import { fetchProvider, type ModelWriteResult, updateProvider } from "@/lib/api";
import { qk } from "@/lib/api/keys";
import { errMsg } from "@/lib/format";
import { useConfirm } from "@/lib/hooks/use-confirm";
import { useToast } from "@/lib/hooks/use-toast";
import { useDeleteProvider, useProvider, useProviders } from "@/lib/hooks/use-providers";
import { ProviderDefForm } from "@/components/system/provider-def-form";

// 云服务商面板:左 sticky 列表(名称/模型数/启用开关/增删)+ 右详情表单(范式同模型面板)。
// 启用开关直写服务端(取详情 → 翻转 enabled → PUT),不经表单、不切选中 → 无需 dirty 守卫;
// 列表切换仍走 dirty 守卫。selected:undefined=未选(默认第一个);null=创建态;string=已选服务商。
export function ProvidersPanel() {
  const list = useProviders();
  const [selected, setSelected] = useState<string | null | undefined>(undefined);
  const [createNonce, setCreateNonce] = useState(0);
  const dirtyRef = useRef(false);
  const confirm = useConfirm();
  const toast = useToast();
  const queryClient = useQueryClient();

  const items = [...(list.data ?? [])].sort((a, b) => a.name.localeCompare(b.name));
  const effSelected = selected === undefined ? (items[0]?.name ?? null) : selected;
  const detail = useProvider(effSelected);
  const del = useDeleteProvider();

  // 启用开关直写:取详情 → 翻转 enabled → PUT。migrate=false(无改名);成功后前缀失效
  // list+detail(选中服务商若未被编辑,表单经 useSyncedForm 跟随刷新回映新 enabled)。
  const toggle = useMutation({
    mutationFn: async ({ name, enabled }: { name: string; enabled: boolean }) => {
      const def = await fetchProvider(name);
      return updateProvider(name, { ...def, enabled }, false);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: qk.providerDefs });
    },
    onError: (e: unknown) => toast.error(errMsg(e)),
  });

  // 切换前 dirty 守卫:dirty 则确认。
  const guard = async (): Promise<boolean> =>
    !dirtyRef.current
    || await confirm({
      title: "放弃未保存的修改?",
      description: "当前服务商有未保存修改,切换将丢弃。",
      confirmText: "放弃",
      cancelText: "继续编辑",
      danger: true,
    });

  const selectProvider = async (name: string) => {
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
      title: `删除服务商 ${name}?`,
      description: "将删除该服务商的模型与映射配置;请求与用量记录保留。",
      confirmText: "删除",
      cancelText: "取消",
      danger: true,
    });
    if (!ok) return;
    del.mutate(name, {
      onSuccess: () => {
        setSelected(undefined);
        dirtyRef.current = false;
        toast.success(`已删除服务商「${name}」`);
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
        <ProviderDefForm
          key={formKey}
          provider={detail.data}
          onSaved={onSaved}
          onDirtyChange={(d) => {
            dirtyRef.current = d;
          }}
        />
      );
  } else {
    formArea = (
      <ProviderDefForm
        key={formKey}
        provider={null}
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
      {/* 左栏:服务商列表(按名称升序)+ 底部操作(新增 / 删除),吸顶后独立滚动。
          行内启用开关独立于行按钮(两者均非嵌套 button,合法 DOM)。 */}
      <div className="flex flex-col gap-1 lg:sticky lg:top-[134px] lg:max-h-[calc(100dvh-150px)] lg:overflow-y-auto">
        {list.isLoading && (
          <Loading />
        )}
        <div className="flex flex-col gap-0.5" role="listbox" aria-label="服务商列表">
          {items.map((m) => {
            const selected = m.name === effSelected;
            return (
              <div
                key={m.name}
                className={
                  "flex items-center gap-2 rounded-md px-3 py-2 transition-colors duration-(--motion-base) " +
                  (selected ? "bg-primary-accent/12" : "hover:bg-card-hover")
                }
              >
                <button
                  type="button"
                  role="option"
                  aria-selected={selected}
                  onClick={() => selectProvider(m.name)}
                  className="min-w-0 flex-1 text-left"
                >
                  <div className={"truncate text-sm " + (selected ? "font-medium text-primary-accent" : "font-medium text-foreground")}>
                    {m.name}
                  </div>
                  <div className="truncate text-xs text-muted-foreground">{m.model_count} 模型</div>
                </button>
                <Switch
                  checked={m.enabled}
                  disabled={toggle.isPending}
                  onChange={() => toggle.mutate({ name: m.name, enabled: !m.enabled })}
                />
              </div>
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
              className="w-full justify-start text-destructive"
              onClick={onDelete}
            >
              删除
            </Button>
          )}
          {del.error && (
            <div className="px-3 text-xs text-destructive">
              删除失败:{errMsg(del.error)}
            </div>
          )}
        </div>
      </div>

      {/* 右栏:详情表单,随页面滚动;保存确认流在表单内,删除按钮在左栏底部 */}
      <div>
        {formArea}
      </div>
    </div>
  );
}
