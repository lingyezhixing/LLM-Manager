import { useEffect, useState } from "react";
import { ConfigSaveBar } from "@/components/config-save-bar";
import { Field, NumberInput, Select, TextInput } from "@/components/ui/form";
import { type ProgramConfig } from "@/lib/api";
import { useConfig, useUpdateProgram } from "@/lib/use-config";

const LOG_LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"];

function shallowEqual(a: ProgramConfig, b: ProgramConfig): boolean {
  return (Object.keys(a) as (keyof ProgramConfig)[]).every((k) => a[k] === b[k]);
}

export function GeneralPanel() {
  const { data, isLoading } = useConfig();
  const update = useUpdateProgram();
  const [form, setForm] = useState<ProgramConfig | null>(data?.program ?? null);

  // 初值就绪时填表单(仅一次;后续 data 刷新由 dirty 比较处理,不打断编辑)。
  useEffect(() => {
    if (data?.program && form === null) setForm(data.program);
  }, [data, form]);

  if (isLoading || !form) {
    return <div className="text-sm text-muted-foreground">加载中…</div>;
  }

  const initial = data!.program;
  const dirty = !shallowEqual(form, initial);
  const portValid = form.port >= 1 && form.port <= 65535;
  const aliveValid = form.alive_time >= 0;
  const set = <K extends keyof ProgramConfig>(k: K, v: ProgramConfig[K]) =>
    setForm({ ...form, [k]: v });
  const num = (s: string): number => (s === "" ? 0 : Number(s));

  return (
    <div>
      <Field label="监听地址 (host)" hint="🔴 需重启" htmlFor="cfg-host">
        <TextInput id="cfg-host" value={form.host} onChange={(e) => set("host", e.target.value)} />
      </Field>
      <Field label="监听端口 (port)" hint="🔴 需重启 · 1–65535" htmlFor="cfg-port"
        error={!portValid ? "端口须在 1–65535" : null}>
        <NumberInput id="cfg-port" value={form.port} onChange={(e) => set("port", num(e.target.value))} />
      </Field>
      <Field label="空闲检测 (alive_time)" hint="🟢 即时生效（读穿）· 秒" htmlFor="cfg-alive"
        error={!aliveValid ? "须 ≥ 0" : null}>
        <NumberInput id="cfg-alive" value={form.alive_time} onChange={(e) => set("alive_time", num(e.target.value))} />
      </Field>
      <Field label="日志级别 (log_level)" hint="🔴 需重启（L1 降级）" htmlFor="cfg-level">
        <Select id="cfg-level" value={form.log_level} onChange={(e) => set("log_level", e.target.value)}>
          {LOG_LEVELS.map((lv) => (
            <option key={lv} value={lv}>{lv}</option>
          ))}
        </Select>
      </Field>
      <Field label="日志目录 (log_dir)" hint="🔴 需重启" htmlFor="cfg-logdir">
        <TextInput id="cfg-logdir" value={form.log_dir} onChange={(e) => set("log_dir", e.target.value)} />
      </Field>
      <Field label="数据库路径 (db_path)" hint="🔴 需重启 · 改需手动迁移文件" htmlFor="cfg-db">
        <TextInput id="cfg-db" value={form.db_path} onChange={(e) => set("db_path", e.target.value)} />
      </Field>
      <Field label="Claude settings 路径 (claude_settings_path)" hint="🔴 需重启" htmlFor="cfg-claude">
        <TextInput id="cfg-claude" value={form.claude_settings_path} onChange={(e) => set("claude_settings_path", e.target.value)} />
      </Field>

      {dirty && (
        <ConfigSaveBar
          saving={update.isPending}
          error={update.error ? "保存失败,请重试" : null}
          onSave={() => update.mutate(form)}
          onReset={() => setForm(initial)}
        />
      )}
    </div>
  );
}
