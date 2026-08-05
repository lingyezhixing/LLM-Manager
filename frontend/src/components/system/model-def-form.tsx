import { useEffect, useState } from "react";
import { ConfigSaveBar } from "@/components/config-save-bar";
import { Button } from "@/components/ui/button";
import { Field, NumberInput, Select, Switch, TextInput } from "@/components/ui/form";
import { numFromStr as num } from "@/lib/format";
import { StringListEditor } from "@/components/ui/repeatable-fields";
import { PricingEditor } from "@/components/system/pricing-editor";
import { SchemeEditor } from "@/components/system/scheme-editor";
import { useConfirm } from "@/components/ui/dialog";
import { type ModelDef, type ModelWriteResult, type SchemeDef } from "@/lib/api";
import { useCreateModelDef, useUpdateModelDef } from "@/lib/hooks/use-model-defs";

const MODES = ["Chat", "Embedding", "Reranker"];

// 空模型草稿(新增用):一个空 scheme。
function emptyModel(): ModelDef {
  return {
    name: "",
    mode: "Chat",
    port: 0,
    auto_start: false,
    aliases: [],
    schemes: [
      {
        config_source: "default",
        required_devices: [],
        command: { exe: "", args: [], env: {}, cwd: null, conda_env: null },
        memory_mb: {},
      },
    ],
    pricing: { pricing_type: "tier", hourly_price: 0, support_cache: false, tiers: [] },
  };
}

// 深拷贝(字段全 JSON 可序列化)。用于隔离 form/baseline 与查询缓存。
const clone = <T,>(x: T): T => JSON.parse(JSON.stringify(x)) as T;

// 草稿 vs baseline 深相等:字段全 JSON 可序列化且键序稳定(均经 clone/cleanPayload 同构构造),
// 故用 JSON.stringify 比较 —— 与 clone 的序列化机制一致,语义统一。
const deepEqual = (a: ModelDef, b: ModelDef): boolean => JSON.stringify(a) === JSON.stringify(b);

// 客户端门控:明显空缺则禁用保存(M6)。
function clientValid(form: ModelDef): boolean {
  if (!form.name.trim()) return false;
  if (form.aliases.length === 0 || form.aliases.some((a) => !a.trim())) return false;
  if (form.schemes.length === 0) return false;
  return form.schemes.every(
    (s) => s.config_source.trim() !== "" && s.command.exe.trim() !== "",
  );
}

// 保存前清理:去 args/required_devices 空串、env/memory_mb 空键(防 argv 传空参)。
function stripEmptyKeys(rec: Record<string, string | number>): Record<string, string | number> {
  const out: Record<string, string | number> = {};
  for (const [k, v] of Object.entries(rec)) {
    if (k.trim() !== "") out[k] = v;
  }
  return out;
}
function cleanPayload(m: ModelDef): ModelDef {
  return {
    ...m,
    schemes: m.schemes.map((s) => {
      // memory_mb:每个 required_device 必须有条目(缺则补 0)。0 = 该设备仅用于方案匹配、
      // 不占显存预算(调度时空缺与 0 等价,都不检查显存);显式持久化 0 让「所见即所存」,
      // 消除前端显示 0(合并默认)但 DB 存空 {} 的假象。
      const memory_mb: Record<string, number> = {};
      for (const d of s.required_devices) {
        if (d.trim() !== "") memory_mb[d] = s.memory_mb[d] ?? 0;
      }
      for (const [k, v] of Object.entries(s.memory_mb)) {
        if (k.trim() !== "" && !(k in memory_mb)) memory_mb[k] = v;   // 保留 required 之外的显存条目
      }
      return {
        ...s,
        required_devices: s.required_devices.filter((d) => d !== ""),
        command: {
          ...s.command,
          args: s.command.args.filter((a) => a !== ""),
          env: stripEmptyKeys(s.command.env) as Record<string, string>,
        },
        memory_mb,
      };
    }),
  };
}

interface ModelDefFormProps {
  // 编辑态:服务端 ModelDef(非空)。创建态:null。
  model: ModelDef | null;
  // 保存成功回调(向上传递写结果与模型名;创建态 result 为空)。
  onSaved: (result: ModelWriteResult, savedName: string) => void;
  // dirty 变化回调(面板用于切换守卫)。
  onDirtyChange?: (dirty: boolean) => void;
}

export function ModelDefForm({ model, onSaved, onDirtyChange }: ModelDefFormProps) {
  const isCreate = model === null;
  const [form, setForm] = useState<ModelDef>(() => (model ? clone(model) : emptyModel()));
  const [baseline, setBaseline] = useState<ModelDef>(() => (model ? clone(model) : emptyModel()));

  // key 变化(切模型/进出创建)由父级触发 remount → useState 重取初值;同模型内不重置。
  useEffect(() => {
    onDirtyChange?.(isCreate || !deepEqual(form, baseline));
  }, [isCreate, form, baseline, onDirtyChange]);

  const confirm = useConfirm();
  const update = useUpdateModelDef(model?.name ?? "");
  const create = useCreateModelDef();
  const mutation = isCreate ? create : update;
  const saving = mutation.isPending;
  const errorMsg = mutation.error ? (mutation.error as Error).message : null;

  const dirty = isCreate ? true : !deepEqual(form, baseline);
  const portValid = form.port >= 1 && form.port <= 65535;
  const canSave = clientValid(form) && portValid;

  const set = <K extends keyof ModelDef>(k: K, v: ModelDef[K]) => setForm({ ...form, [k]: v });

  const setScheme = (i: number, next: SchemeDef) =>
    setForm({ ...form, schemes: form.schemes.map((s, idx) => (idx === i ? next : s)) });
  const removeScheme = (i: number) =>
    setForm({ ...form, schemes: [...form.schemes.slice(0, i), ...form.schemes.slice(i + 1)] });
  const addScheme = () =>
    setForm({
      ...form,
      schemes: [
        ...form.schemes,
        {
          config_source: "default",
          required_devices: [],
          command: { exe: "", args: [], env: {}, cwd: null, conda_env: null },
          memory_mb: {},
        },
      ],
    });

  const doUpdate = (migrate: boolean) => {
    const payload = cleanPayload(form);
    setForm(payload);
    // F1:baseline 仅在保存成功后推进,失败时 dirty 不丢。
    update.mutate(
      { body: payload, migrate },
      {
        onSuccess: (result) => {
          setBaseline(clone(payload));
          onSaved(result, payload.name);   // 传新名:改名后 panel 切到新名
        },
      },
    );
  };

  const onSave = async () => {
    if (saving) return;   // 防重复提交(ConfigSaveBar 已 disable 按钮,此为函数级双保险)
    if (isCreate) {
      const payload = cleanPayload(form);
      setForm(payload);
      create.mutate(payload, {
        onSuccess: () => {
          setBaseline(clone(payload));
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
    <div>
      <div className="mb-1 text-sm font-medium text-foreground">基本</div>
      <div className="grid grid-cols-1 gap-x-6 sm:grid-cols-2">
        <Field
          label="名称"
          hint={isCreate ? "唯一标识" : "可改名(保存时会询问是否迁移历史数据)"}
          htmlFor="mdf-name"
        >
          <TextInput
            id="mdf-name"
            value={form.name}
            onChange={(e) => set("name", e.target.value)}
          />
        </Field>
        <Field label="模式" hint="选择健康探测方式" htmlFor="mdf-mode">
          <Select id="mdf-mode" value={form.mode} onChange={(e) => set("mode", e.target.value)}>
            {MODES.map((m) => (
              <option key={m} value={m}>{m}</option>
            ))}
          </Select>
        </Field>
        <Field label="端口" hint="模型服务监听端口" htmlFor="mdf-port" error={!portValid && form.port !== 0 ? "端口须在 1–65535" : null}>
          <NumberInput id="mdf-port" value={form.port} onChange={(e) => set("port", num(e.target.value))} />
        </Field>
        <Field label="自启动" hint="程序启动时自动拉起该模型" htmlFor="mdf-auto">
          <div className="flex items-center gap-2">
            <Switch id="mdf-auto" checked={form.auto_start} onChange={(v) => set("auto_start", v)} />
            <span className="text-xs text-muted-foreground">{form.auto_start ? "开" : "关"}</span>
          </div>
        </Field>
        <Field
          className="sm:col-span-2"
          label="对外别名"
          hint="客户端请求用的名字;第一个 = 下游服务名(lmdeploy --model-name / llama.cpp -a)"
        >
          <StringListEditor
            values={form.aliases}
            onChange={(aliases) => set("aliases", aliases)}
            placeholder="glm-4.6"
          />
        </Field>
      </div>

      <div className="mb-1 mt-4 flex items-center justify-between">
        <span className="text-sm font-medium text-foreground">启动方案</span>
        <Button type="button" size="sm" variant="ghost" onClick={addScheme}>+ 添加方案</Button>
      </div>
      <p className="mb-3 text-xs text-muted-foreground">
        每个方案 = 一套启动配置;运行时按在线设备自动选匹配的方案(多 GPU 或备用配置时才需多套,一般一套即可)。
      </p>
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

      {(dirty || isCreate) && (
        <div className="mt-3">
          <ConfigSaveBar
            saving={saving}
            error={errorMsg}
            onSave={onSave}
            onReset={() => setForm(clone(baseline))}
            saveDisabled={!canSave}
          />
          {!canSave && (
            <p className="mt-2 text-xs text-warning">
              需:名称、≥1 别名、≥1 方案(每方案:标识与命令行非空)、端口 1–65535
            </p>
          )}
        </div>
      )}
    </div>
  );
}
