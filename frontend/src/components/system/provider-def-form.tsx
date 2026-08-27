import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Field, Select, Switch, TextInput } from "@/components/ui/form";
import { KeyValueEditor } from "@/components/ui/repeatable-fields";
import { TierEditor } from "@/components/system/tier-editor";
import { errMsg } from "@/lib/format";
import { updateProvider, type CloudMapping, type CloudModel, type CloudTimeWindow, type ProviderDef } from "@/lib/api";
import { useConfirm } from "@/lib/hooks/use-confirm";
import { useToast } from "@/lib/hooks/use-toast";
import { useCreateProvider, useUpdateProvider } from "@/lib/hooks/use-providers";
import { useSyncedForm } from "@/lib/hooks/use-synced-form";
import { clone, deepEqual, emptyCloudModel, emptyProvider, hhmmToMinutes, minutesToHhmm } from "@/lib/provider-def";

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

  // 模型区编辑 helper。
  const setModel = (i: number, next: CloudModel) => setForm({ ...form, models: form.models.map((m, idx) => idx === i ? next : m) });
  const removeModel = (i: number) => setForm({ ...form, models: [...form.models.slice(0, i), ...form.models.slice(i + 1)] });
  const addModel = () => setForm({ ...form, models: [...form.models, emptyCloudModel()] });
  // 谷时段:HH:MM 严格解析,非法输入忽略该次变更(值恒由分钟反推,非法态进不了状态)。
  const setWindow = (i: number, wi: number, k: "start_min" | "end_min", raw: string) => {
    const min = hhmmToMinutes(raw);
    if (min === null) return;
    const m = form.models[i];
    setModel(i, { ...m, peak_windows: m.peak_windows.map((w, idx) => (idx === wi ? ({ ...w, [k]: min } as CloudTimeWindow) : w)) });
  };
  const addWindow = (i: number) => {
    const m = form.models[i];
    setModel(i, { ...m, peak_windows: [...m.peak_windows, { start_min: 480, end_min: 1320 }] });
  };
  const removeWindow = (i: number, wi: number) => {
    const m = form.models[i];
    setModel(i, { ...m, peak_windows: m.peak_windows.filter((_, idx) => idx !== wi) });
  };
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
          <div key={i} className="overflow-hidden rounded-md border border-border">
            {/* 身份行:编号并入名称 label(模型 1/2…),开关带状态文字,删除收进行尾;下缘细线与计费体分隔 */}
            <div className="flex flex-wrap items-end gap-x-3 gap-y-2 border-b border-border-subtle px-3 pb-1 pt-2">
              <Field label={`模型 ${i + 1}`} className="min-w-44 flex-1">
                <TextInput value={m.model_name} placeholder="如 deepseek-chat"
                  onChange={(e) => setModel(i, { ...m, model_name: e.target.value })} />
              </Field>
              <Field label="支持缓存">
                <div className="flex h-9 items-center gap-2">
                  <Switch checked={m.support_cache} onChange={(v) => setModel(i, { ...m, support_cache: v })} />
                  <span className="text-xs text-muted-foreground">{m.support_cache ? "开" : "关"}</span>
                </div>
              </Field>
              <Field label="峰谷双价">
                <div className="flex h-9 items-center gap-2">
                  <Switch checked={m.dual_pricing} onChange={(v) => setModel(i, { ...m, dual_pricing: v })} />
                  {m.dual_pricing
                    ? <span className="text-xs text-primary-accent">峰价启用</span>
                    : <span className="text-xs text-muted-foreground">关</span>}
                </div>
              </Field>
              <button type="button" aria-label={`删除模型 ${i + 1}`}
                className="mb-4 h-9 shrink-0 px-2 text-xs text-muted-foreground hover:text-destructive"
                onClick={() => removeModel(i)}>✕</button>
            </div>

            {/* 基础阶梯:峰谷双价关闭时的唯一计价口径(即基础/谷价) */}
            <div className="px-3 pb-2 pt-1">
              <div className="text-xs text-muted-foreground">基础阶梯价格(即基础/谷价;元/百万 token)</div>
              <TierEditor tiers={m.tiers_base} supportCache={m.support_cache}
                onChange={(next) => setModel(i, { ...m, tiers_base: next })} />
            </div>

            {m.dual_pricing && (
              /* 峰谷子面板:浅底内嵌区紧随其开关之下,收纳峰时段与峰价,与基础区隔开 */
              <div className="border-t border-dashed border-border-subtle bg-card-2 px-3 pb-2 pt-1">
                <div className="flex items-center justify-between">
                  <span className="text-xs text-muted-foreground">峰时段(服务器本地时间;须在同一天内,起 &lt; 止)</span>
                  <Button type="button" size="sm" variant="ghost" onClick={() => addWindow(i)}>+ 添加时段</Button>
                </div>
                <div className="flex flex-col gap-1">
                  {m.peak_windows.map((w, wi) => (
                    <div key={wi} className="flex items-end gap-x-3">
                      <Field label="开始(HH:MM)" className="w-28">
                        <TextInput value={minutesToHhmm(w.start_min) ?? ""} placeholder="08:00"
                          onChange={(e) => setWindow(i, wi, "start_min", e.target.value)} />
                      </Field>
                      <span aria-hidden className="mb-4 flex h-9 items-center text-muted-foreground">–</span>
                      <Field label="结束(HH:MM)" className="w-28">
                        <TextInput value={minutesToHhmm(w.end_min) ?? ""} placeholder="22:00"
                          onChange={(e) => setWindow(i, wi, "end_min", e.target.value)} />
                      </Field>
                      <button type="button" aria-label={`删除时段 ${wi + 1}`}
                        className="mb-4 h-9 shrink-0 px-2 text-xs text-muted-foreground hover:text-destructive"
                        onClick={() => removeWindow(i, wi)}>✕</button>
                    </div>
                  ))}
                  {m.peak_windows.length === 0 && (
                    <p className="text-micro leading-relaxed text-muted-foreground">
                      尚未添加峰时段——保存校验要求至少一段;未命中峰时段的请求按基础阶梯计价。
                    </p>
                  )}
                </div>
                <div className="text-xs text-muted-foreground">峰价阶梯(元/百万 token;请求完成时刻落在任一峰时段按此计价,整单判定)</div>
                <TierEditor tiers={m.tiers_peak} supportCache={m.support_cache}
                  onChange={(next) => setModel(i, { ...m, tiers_peak: next })} />
              </div>
            )}
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
