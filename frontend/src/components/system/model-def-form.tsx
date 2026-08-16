import { useEffect } from "react";
import { Button } from "@/components/ui/button";
import { Field, NumberInput, Select, Switch, TextInput } from "@/components/ui/form";
import { numFromStr as num, portError } from "@/lib/format";
import { StringListEditor } from "@/components/ui/repeatable-fields";
import { PricingEditor } from "@/components/system/pricing-editor";
import { SchemeEditor } from "@/components/system/scheme-editor";
import { useConfirm } from "@/lib/hooks/use-confirm";
import { type ModelDef, type ModelWriteResult, type SchemeDef } from "@/lib/api";
import { useCreateModelDef, useUpdateModelDef } from "@/lib/hooks/use-model-defs";
import { useSyncedForm } from "@/lib/hooks/use-synced-form";
import { MODES, clientValid, cleanPayload, clone, deepEqual, emptyModel, emptyScheme } from "@/lib/model-def";

interface ModelDefFormProps {
  // 编辑态:服务端 ModelDef(非空)。创建态:null。
  model: ModelDef | null;
  // 保存成功回调(向上传递写结果与模型名;创建态 result 为空)。
  onSaved: (result: ModelWriteResult, savedName: string) => void;
  // dirty 变化回调(面板用于切换守卫)。
  onDirtyChange?: (dirty: boolean) => void;
  // 删除当前模型(名称行末格按钮;panel 层负责 confirm + 删除)。
  onDelete?: () => void;
}

export function ModelDefForm({ model, onSaved, onDirtyChange, onDelete }: ModelDefFormProps) {
  const isCreate = model === null;
  // useSyncedForm:key 变化(切模型/进出创建)由父级触发 remount → 重取初值;同模型内不重置。
  // alwaysDirty=true(创建态:空表单也算 dirty,保存按钮常显)。
  const { form, setForm, dirty, commit, reset } = useSyncedForm<ModelDef>(
    null, // 不进外部刷新跟随:本表单由 key remount 管理生命周期
    model ? clone(model) : emptyModel(),
    deepEqual,
    { alwaysDirty: isCreate },
  );

  useEffect(() => {
    onDirtyChange?.(dirty);
  }, [dirty, onDirtyChange]);

  const confirm = useConfirm();
  const update = useUpdateModelDef(model?.name ?? "");
  const create = useCreateModelDef();
  const mutation = isCreate ? create : update;
  const saving = mutation.isPending;
  const errorMsg = mutation.error ? (mutation.error as Error).message : null;

  const portValid = portError(form.port) === null;
  const canSave = clientValid(form) && portValid;

  const set = <K extends keyof ModelDef>(k: K, v: ModelDef[K]) => setForm({ ...form, [k]: v });

  const setScheme = (i: number, next: SchemeDef) =>
    setForm({ ...form, schemes: form.schemes.map((s, idx) => (idx === i ? next : s)) });
  const removeScheme = (i: number) =>
    setForm({ ...form, schemes: [...form.schemes.slice(0, i), ...form.schemes.slice(i + 1)] });
  const addScheme = () =>
    setForm({
      ...form,
      schemes: [...form.schemes, emptyScheme()],
    });

  const doUpdate = (migrate: boolean) => {
    const payload = cleanPayload(form);
    setForm(payload);
    // F1:baseline 仅在保存成功后推进,失败时 dirty 不丢。
    update.mutate(
      { body: payload, migrate },
      {
        onSuccess: (result) => {
          commit(clone(payload));
          onSaved(result, payload.name);   // 传新名:改名后 panel 切到新名
        },
      },
    );
  };

  const onSave = async () => {
    if (saving) return;   // 防重复提交(保存按钮已 disable,此为函数级双保险)
    if (isCreate) {
      const payload = cleanPayload(form);
      setForm(payload);
      create.mutate(payload, {
        onSuccess: () => {
          commit(clone(payload));
          onSaved({ affected_routing: [], hint: null }, payload.name);
        },
      });
      return;
    }
    // 编辑态改名(精确比较,与后端 body.name != name 一致——单边 trim 会造成前后端判定不一致):
    // 询问是否迁移历史数据(二元;false=不迁移但仍保存)
    if (form.name !== model!.name) {
      const migrate = await confirm({
        title: "改名:是否迁移历史数据?",
        description: "两种都会保存改名。迁移 → 用量/成本/日志归到新名,统计连续;不迁移 → 旧名变孤立模型、新名从零。",
        confirmText: "迁移",
        cancelText: "不迁移(保留旧名)",
      });
      doUpdate(migrate);
    } else {
      doUpdate(false);
    }
  };

  return (
    <div className="relative pb-6">
      {/* 右上角提示浮层:保存失败 / 保存条件未满足(不与右下浮动按钮争空间) */}
      {(errorMsg || !canSave) && (
        <div className="absolute right-0 top-0 z-20 w-64 rounded-lg border border-border bg-card px-3 py-2 text-xs shadow-lg">
          {errorMsg && <p className="text-destructive">{errorMsg}</p>}
          {!canSave && (
            <p className={errorMsg ? "mt-1 text-warning" : "text-warning"}>
              需:名称、≥1 别名、≥1 方案(每方案:标识与命令行非空)、端口 1–65535
            </p>
          )}
        </div>
      )}
      <div className="mb-1 text-sm font-medium text-foreground">基本</div>
      {/* 名称行 4:2:2:1:1(名称/模式/端口/自启动/删除);创建态无删除,末格留白保比例。 */}
      <div className="grid grid-cols-1 gap-x-6 sm:grid-cols-10">
        <Field
          className="sm:col-span-4"
          label="名称"
          htmlFor="mdf-name"
        >
          <TextInput
            id="mdf-name"
            value={form.name}
            onChange={(e) => set("name", e.target.value)}
          />
        </Field>
        <Field className="sm:col-span-2" label="模式" htmlFor="mdf-mode">
          <Select id="mdf-mode" value={form.mode} onChange={(e) => set("mode", e.target.value)}>
            {MODES.map((m) => (
              <option key={m} value={m}>{m}</option>
            ))}
          </Select>
        </Field>
        <Field className="sm:col-span-2" label="端口" htmlFor="mdf-port" error={form.port !== 0 ? portError(form.port) : null}>
          <NumberInput id="mdf-port" value={form.port} onChange={(e) => set("port", num(e.target.value))} />
        </Field>
        <Field className="sm:col-span-1" label="自启动" htmlFor="mdf-auto">
          <div className="flex h-9 items-center gap-2">
            <Switch id="mdf-auto" checked={form.auto_start} onChange={(v) => set("auto_start", v)} />
            <span className="text-xs text-muted-foreground">{form.auto_start ? "开" : "关"}</span>
          </div>
        </Field>
        {onDelete && !isCreate ? (
          <div className="mb-4 flex items-end sm:col-span-1">
            <Button type="button" size="sm" variant="destructive" onClick={onDelete} className="h-9 w-full">
              删除模型
            </Button>
          </div>
        ) : (
          <div className="hidden sm:col-span-1 sm:block" />
        )}
        <Field
          className="sm:col-span-10"
          label="对外别名"
        >
          <StringListEditor
            values={form.aliases}
            onChange={(aliases) => set("aliases", aliases)}
          />
        </Field>
      </div>

      <div className="mb-1 mt-4 flex items-center justify-between">
        <span className="text-sm font-medium text-foreground">启动方案</span>
        <Button type="button" size="sm" variant="ghost" onClick={addScheme}>+ 添加方案</Button>
      </div>
      <div className="flex flex-col gap-3">
        {/* 命令变量:{{port}}/{{alias}} → 顶部端口/第一别名,实时预览替换(与后端 substitute_vars 一致) */}
        {form.schemes.map((s, i) => (
          <SchemeEditor
            key={i}
            value={s}
            index={i}
            vars={{ "{{port}}": String(form.port), "{{alias}}": form.aliases[0] ?? "" }}
            onChange={(next) => setScheme(i, next)}
            onRemove={() => removeScheme(i)}
          />
        ))}
      </div>

      <div className="mb-1 mt-4 text-sm font-medium text-foreground">计费</div>
      <PricingEditor value={form.pricing} onChange={(pricing) => set("pricing", pricing)} />

      {/* 右下角浮动保存/重置(仅 dirty/创建态显示):高频操作,滚动时始终可见。 */}
      {(dirty || isCreate) && (
        <div className="sticky bottom-4 z-10 mt-3 flex flex-col items-end gap-2">
          <Button type="button" variant="outline" onClick={() => reset()}>
            重置
          </Button>
          <Button type="button" onClick={onSave} disabled={saving || !canSave}>
            {saving ? "保存中…" : "保存"}
          </Button>
        </div>
      )}
    </div>
  );
}
