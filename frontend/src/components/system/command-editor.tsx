import { Field, TextInput } from "@/components/ui/form";
import { KeyValueEditor, StringListEditor } from "@/components/ui/repeatable-fields";
import type { CommandDef } from "@/lib/api";

// 一个 command 块:exe / args(有序)/ env(键=值)/ cwd / conda_env。
// 受控:父级持 CommandDef。cwd/conda_env 在表单内 null↔"" 转换(文本框用空串)。
export function CommandEditor({
  value,
  onChange,
}: {
  value: CommandDef;
  onChange: (next: CommandDef) => void;
}) {
  const set = <K extends keyof CommandDef>(k: K, v: CommandDef[K]) =>
    onChange({ ...value, [k]: v });
  const toStr = (s: string | null): string => s ?? "";
  const fromStr = (s: string): string | null => (s === "" ? null : s);

  return (
    <div className="rounded-md border border-border p-3">
      <Field label="exe(可执行文件)" htmlFor="cmd-exe">
        <TextInput id="cmd-exe" value={value.exe} onChange={(e) => set("exe", e.target.value)} />
      </Field>
      <Field label="args(参数,有序)" hint="每行一个;含空格安全">
        <StringListEditor
          values={value.args}
          onChange={(args) => set("args", args)}
          placeholder="--port 8000"
        />
      </Field>
      <Field label="env(环境变量)" hint="键 = 值">
        <KeyValueEditor
          entries={value.env}
          onChange={(env) => set("env", env as Record<string, string>)}
          valuePlaceholder="0"
        />
      </Field>
      <Field label="cwd(工作目录,可空)" htmlFor="cmd-cwd">
        <TextInput
          id="cmd-cwd"
          value={toStr(value.cwd)}
          onChange={(e) => set("cwd", fromStr(e.target.value))}
        />
      </Field>
      <Field label="conda_env(conda 环境名,可空)" htmlFor="cmd-conda">
        <TextInput
          id="cmd-conda"
          value={toStr(value.conda_env)}
          onChange={(e) => set("conda_env", fromStr(e.target.value))}
        />
      </Field>
    </div>
  );
}
