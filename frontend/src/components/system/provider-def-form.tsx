import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Field, Select, Switch, TextInput } from "@/components/ui/form";
import { KeyValueEditor } from "@/components/ui/repeatable-fields";
import { TierEditor } from "@/components/system/tier-editor";
import { errMsg } from "@/lib/format";
import { updateProvider, type CloudMapping, type CloudModel, type ProviderDef } from "@/lib/api";
import { useConfirm } from "@/lib/hooks/use-confirm";
import { useToast } from "@/lib/hooks/use-toast";
import { useCreateProvider, useUpdateProvider } from "@/lib/hooks/use-providers";
import { useSyncedForm } from "@/lib/hooks/use-synced-form";
import { clone, deepEqual, emptyCloudModel, emptyProvider } from "@/lib/provider-def";

interface ProviderDefFormProps {
  // 编辑态:服务端 ProviderDef(非空)。创建态:null。
  provider: ProviderDef | null;
  // 保存成功回调(向上传递写结果与保存名;创建态 result 为空)。
  onSaved: (result: { affected_routing: string[]; hint: string | null }, savedName: string) => void;
  // dirty 变化回调(面板用于切换守卫)。
  onDirtyChange?: (dirty: boolean) => void;
  // 删除当前服务商(panel 层负责 confirm + 删除;表单内无删除按钮)。
  onDelete?: () => void;
}

export function ProviderDefForm({ provider, onSaved, onDirtyChange }: ProviderDefFormProps) {
  const isCreate = provider === null;
  // useSyncedForm:key 变化(切服务商/进出创建)由父级触发 remount → 重取初值;同服务商内不重置。
  // alwaysDirty=true(创建态:空表单也算 dirty,保存按钮常显)。
  const { form, setForm, dirty, commit, reset } = useSyncedForm<ProviderDef>(
    null,
    provider ? clone(provider) : emptyProvider(),
    deepEqual,
    { alwaysDirty: isCreate },
  );

  useEffect(() => {
    onDirtyChange?.(dirty);
  }, [dirty, onDirtyChange]);

  const create = useCreateProvider();
  const update = useUpdateProvider(provider?.name ?? "");
  const mutation = isCreate ? create : update;
  const confirm = useConfirm();
  const toast = useToast();
  const [confirming, setConfirming] = useState(false);
  const saving = mutation.isPending || confirming;
  const set = <K extends keyof ProviderDef>(k: K, v: ProviderDef[K]) => setForm({ ...form, [k]: v });

  // 模型区编辑 helper(峰谷控件延后,v3.3.0 不渲染,见设计 §14)。
  const setModel = (i: number, next: CloudModel) => setForm({ ...form, models: form.models.map((m, idx) => idx === i ? next : m) });
  const removeModel = (i: number) => setForm({ ...form, models: [...form.models.slice(0, i), ...form.models.slice(i + 1)] });
  const addModel = () => setForm({ ...form, models: [...form.models, emptyCloudModel()] });
  const setMapping = (i: number, next: CloudMapping) => setForm({ ...form, mappings: form.mappings.map((m, idx) => idx === i ? next : m) });
  const removeMapping = (i: number) => setForm({ ...form, mappings: [...form.mappings.slice(0, i), ...form.mappings.slice(i + 1)] });
  const addMapping = () => setForm({ ...form, mappings: [...form.mappings, { local_path: "", target_url: "", auth_style: "bearer" }] });

  const canSave = form.name.trim() !== "";

  // 编辑态保存:先 dry_run 预检(不落库,复用真实写的一切校验 → 异常直接反馈,不吞),
  // 通过后落库;baseline 仅在保存成功后推进,失败时 dirty 不丢。
  const doUpdate = async (migrate: boolean) => {
    const payload = clone(form);
    try {
      await updateProvider(provider!.name, payload, false, true);
    } catch (e) {
      toast.error(errMsg(e));
      return;
    }
    update.mutate({ body: payload, migrate }, {
      onSuccess: (result) => {
        commit(clone(payload));
        onSaved(result, payload.name);   // 传新名:改名后 panel 切到新名
      },
    });
  };

  const onSave = async () => {
    if (saving) return;   // 防重复提交(保存按钮已 disable,此为函数级双保险)
    setConfirming(true);
    try {
      if (isCreate) {
        create.mutate(form, {
          onSuccess: () => {
            commit(clone(form));
            onSaved({ affected_routing: [], hint: null }, form.name);
          },
        });
        return;
      }
      // 编辑态改名(精确比较,与后端 body.name != name 一致——单边 trim 会造成前后端判定不一致):
      // 询问是否迁移历史数据(二元;false=不迁移但仍保存)
      if (form.name !== provider!.name) {
        const migrate = await confirm({
          title: "改名:是否迁移历史数据?",
          description: "两种都会保存改名。迁移 → 用量/成本归到新名,统计连续;不迁移 → 旧名变孤立模型。",
          confirmText: "迁移",
          cancelText: "不迁移(保留旧名)",
        });
        await doUpdate(migrate);
      } else {
        await doUpdate(false);
      }
    } finally {
      setConfirming(false);
    }
  };

  return (
    <div className="relative pb-6">
      <div className="mb-1 text-sm font-medium text-foreground">基本</div>
      <div className="grid grid-cols-1 gap-x-6 sm:grid-cols-4">
        <Field className="sm:col-span-2" label="名称" htmlFor="pf-name">
          <TextInput id="pf-name" value={form.name} onChange={(e) => set("name", e.target.value)} />
        </Field>
        <Field className="sm:col-span-1" label="启用" htmlFor="pf-enabled">
          <div className="flex h-9 items-center gap-2">
            <Switch id="pf-enabled" checked={form.enabled} onChange={(v) => set("enabled", v)} />
            <span className="text-xs text-muted-foreground">{form.enabled ? "开" : "关"}</span>
          </div>
        </Field>
        <Field className="sm:col-span-1" label="API Key" htmlFor="pf-key">
          <TextInput id="pf-key" type="password" value={form.api_key} onChange={(e) => set("api_key", e.target.value)} />
        </Field>
      </div>

      <div className="mb-1 mt-4 text-sm font-medium text-foreground">端点(留空 = 该接口族不支持)</div>
      <div className="grid grid-cols-1 gap-x-6 sm:grid-cols-3">
        <Field label="OpenAI 传统 base" htmlFor="pf-oa">
          <TextInput id="pf-oa" placeholder="https://api.openai.com/v1" value={form.openai_base}
            onChange={(e) => set("openai_base", e.target.value)} />
        </Field>
        <Field label="Responses base" htmlFor="pf-rs">
          <TextInput id="pf-rs" placeholder="https://api.openai.com/v1" value={form.responses_base}
            onChange={(e) => set("responses_base", e.target.value)} />
        </Field>
        <Field label="Claude base" htmlFor="pf-cl">
          <TextInput id="pf-cl" placeholder="https://api.anthropic.com" value={form.claude_base}
            onChange={(e) => set("claude_base", e.target.value)} />
        </Field>
      </div>

      <div className="mb-1 mt-4 flex items-center justify-between">
        <span className="text-sm font-medium text-foreground">自定义映射</span>
        <Button type="button" size="sm" variant="ghost" onClick={addMapping}>+ 添加映射</Button>
      </div>
      <div className="flex flex-col gap-2">
        {form.mappings.map((m, i) => (
          <div key={i} className="flex flex-wrap items-end gap-x-3 gap-y-2 rounded-md border border-border px-3 py-2">
            <Field label="本地路径" className="min-w-40 flex-1">
              <TextInput value={m.local_path} onChange={(e) => setMapping(i, { ...m, local_path: e.target.value })} />
            </Field>
            <Field label="云端 URL" className="min-w-40 flex-1">
              <TextInput value={m.target_url} onChange={(e) => setMapping(i, { ...m, target_url: e.target.value })} />
            </Field>
            <Field label="鉴权">
              <Select value={m.auth_style} onChange={(e) => setMapping(i, { ...m, auth_style: e.target.value as CloudMapping["auth_style"] })}>
                <option value="bearer">Bearer</option>
                <option value="x-api-key">x-api-key</option>
                <option value="none">无</option>
              </Select>
            </Field>
            <button type="button" className="mb-1 h-9 shrink-0 px-2 text-xs text-muted-foreground hover:text-destructive"
              onClick={() => removeMapping(i)}>✕</button>
          </div>
        ))}
      </div>

      <div className="mb-1 mt-4 text-sm font-medium text-foreground">高级</div>
      <KeyValueEditor
        entries={form.extra_headers as Record<string, string | number>}
        onChange={(next) => set("extra_headers", Object.fromEntries(Object.entries(next).map(([k, v]) => [k, String(v)])))}
      />
      <p className="mt-1 text-xs text-muted-foreground">值支持 {"{key}"} 占位符(替换为 API Key);空值不发送。</p>

      <div className="mb-1 mt-4 flex items-center justify-between">
        <span className="text-sm font-medium text-foreground">模型</span>
        <Button type="button" size="sm" variant="ghost" onClick={addModel}>+ 添加模型</Button>
      </div>
      <div className="flex flex-col gap-3">
        {form.models.map((m, i) => (
          <div key={i} className="rounded-md border border-border px-3 py-2">
            <div className="flex items-center gap-2">
              <Field label="模型名" className="flex-1">
                <TextInput value={m.model_name} onChange={(e) => setModel(i, { ...m, model_name: e.target.value })} />
              </Field>
              <Field label="支持缓存">
                <div className="flex h-9 items-center">
                  <Switch checked={m.support_cache} onChange={(v) => setModel(i, { ...m, support_cache: v })} />
                </div>
              </Field>
              <button type="button" className="h-9 shrink-0 px-2 text-xs text-muted-foreground hover:text-destructive"
                onClick={() => removeModel(i)}>✕</button>
            </div>
            <div className="mt-2 text-xs text-muted-foreground">阶梯价格(元/百万 token)</div>
            <TierEditor tiers={m.tiers_base} supportCache={m.support_cache}
              onChange={(next) => setModel(i, { ...m, tiers_base: next })} />
          </div>
        ))}
      </div>

      {(dirty || isCreate) && (
        <div className="sticky bottom-4 z-10 mt-3 flex flex-col items-end gap-2">
          {mutation.error && (
            <p className="max-w-80 text-right text-xs text-destructive">保存失败:{errMsg(mutation.error)}</p>
          )}
          <div className="flex gap-2">
            <Button type="button" variant="outline" onClick={() => reset()}>重置</Button>
            <Button type="button" onClick={onSave} disabled={saving || !canSave}>
              {saving ? "保存中…" : "保存"}
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
