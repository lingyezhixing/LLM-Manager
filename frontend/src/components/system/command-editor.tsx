import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Field, TextArea, TextInput } from "@/components/ui/form";
import { KeyValueEditor, StringListEditor } from "@/components/ui/repeatable-fields";
import { hasUnterminatedQuote, joinCommandLine, previewCommand, splitCommandLine } from "@/lib/split-command";
import type { CommandDef } from "@/lib/api";

// 命令编辑:顺序=环境变量 → conda 环境 → 命令行 → 预览 → 高级(折叠 exe/args/cwd)。
// 主输入=「命令行」(粘贴整条命令 → 程序拆 argv 直跑,无 shell);env 提到前面更直觉;
// args 列表作命令行解析歧义(带空格路径等)的手改回退。

// 内置常用环境变量(下拉建议,可手输任意键):GPU 可见性设置最常用。
const COMMON_ENV_VARS = [
  "CUDA_VISIBLE_DEVICES",   // NVIDIA:限定可见 GPU(如 0 或 0,1)
  "HIP_VISIBLE_DEVICES",    // AMD ROCm:限定可见 GPU
];
export function CommandEditor({
  value,
  onChange,
  vars,
}: {
  value: CommandDef;
  onChange: (next: CommandDef) => void;
  // 变量替换({{port}}/{{alias}} → 实际值,仅用于「命令预览」;编辑框保持字面占位符)
  vars?: Record<string, string>;
}) {
  const [line, setLine] = useState(() => joinCommandLine(value.exe, value.args));
  const [showAdvanced, setShowAdvanced] = useState(false);
  // 上一次由「命令行」解析出的 exe/args;用于区分「我自己改的」vs「外部改的(reset/高级区)」。
  const lastParsed = useRef({ exe: value.exe, args: value.args });

  // 外部改动(reset / 高级区手改 args/exe)→ 重建命令行文本;自己输入时不重建(保字面量)。
  useEffect(() => {
    const same =
      value.exe === lastParsed.current.exe &&
      value.args.length === lastParsed.current.args.length &&
      value.args.every((a, i) => a === lastParsed.current.args[i]);
    if (!same) {
      setLine(joinCommandLine(value.exe, value.args));
      lastParsed.current = { exe: value.exe, args: value.args };
    }
  }, [value.exe, value.args]);

  const onLine = (text: string) => {
    setLine(text); // 保字面量,避免光标跳/空格被规范化
    const { exe, args } = splitCommandLine(text);
    lastParsed.current = { exe, args };
    onChange({ ...value, exe, args });
  };

  const set = <K extends keyof CommandDef>(k: K, v: CommandDef[K]) =>
    onChange({ ...value, [k]: v });
  const toStr = (s: string | null): string => s ?? "";
  const fromStr = (s: string): string | null => (s === "" ? null : s);

  const preview = previewCommand(value, vars);

  return (
    <div className="rounded-md border border-border p-3">
      <Field label="环境变量" htmlFor="cmd-env">
        <KeyValueEditor
          entries={value.env}
          onChange={(env) => set("env", env as Record<string, string>)}
          keyOptions={COMMON_ENV_VARS}
        />
      </Field>
      <Field
        label="conda 环境"
        htmlFor="cmd-conda"
      >
        <TextInput
          id="cmd-conda"
          value={toStr(value.conda_env)}
          onChange={(e) => set("conda_env", fromStr(e.target.value))}
        />
      </Field>
      <Field label="命令行">
        <TextArea
          value={line}
          onChange={(e) => onLine(e.target.value)}
          rows={4}
        />
      </Field>

      {hasUnterminatedQuote(line) && (
        <p className="-mt-2 mb-3 text-xs text-warning">
          引号未闭合:含空格的参数可能被错误切分(可在下方「高级」区手改 args)
        </p>
      )}

      {preview && (
        <div className="mb-3 rounded-md bg-muted px-3 py-2">
          <div className="mb-0.5 text-xs text-muted-foreground">命令预览</div>
          <code className="block break-all font-mono text-xs text-foreground">{preview}</code>
        </div>
      )}

      <Button type="button" size="sm" variant="ghost" onClick={() => setShowAdvanced((v) => !v)}>
        {showAdvanced ? "▾ 高级(exe / args / cwd)" : "▸ 高级(exe / args / cwd)"}
      </Button>
      {showAdvanced && (
        <div className="mt-3 flex flex-col gap-3 border-t border-border-subtle pt-3">
          <Field label="exe(可执行文件)" htmlFor="cmd-exe">
            <TextInput id="cmd-exe" value={value.exe} onChange={(e) => set("exe", e.target.value)} />
          </Field>
          <Field label="args(参数,有序)">
            <StringListEditor
              values={value.args}
              onChange={(args) => set("args", args)}
            />
          </Field>
          <Field label="cwd(工作目录,可空)" htmlFor="cmd-cwd">
            <TextInput
              id="cmd-cwd"
              value={toStr(value.cwd)}
              onChange={(e) => set("cwd", fromStr(e.target.value))}
            />
          </Field>
        </div>
      )}
    </div>
  );
}
