import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Field, TextArea, TextInput } from "@/components/ui/form";
import { KeyValueEditor, StringListEditor } from "@/components/ui/repeatable-fields";
import { joinCommandLine, previewCommand, splitCommandLine } from "@/lib/split-command";
import type { CommandDef } from "@/lib/api";

// 命令编辑:顺序=环境变量 → conda 环境 → 命令行 → 预览 → 高级(折叠 exe/args/cwd)。
// 主输入=「命令行」(粘贴整条命令 → 程序拆 argv 直跑,无 shell);env 提到前面更直觉;
// args 列表作命令行解析歧义(带空格路径等)的手改回退。
export function CommandEditor({
  value,
  onChange,
}: {
  value: CommandDef;
  onChange: (next: CommandDef) => void;
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

  const preview = previewCommand(value);

  return (
    <div className="rounded-md border border-border p-3">
      <Field label="环境变量" hint="键 = 值;程序会与系统环境合并后传入">
        <KeyValueEditor
          entries={value.env}
          onChange={(env) => set("env", env as Record<string, string>)}
          keyPlaceholder="CUDA_VISIBLE_DEVICES"
          valuePlaceholder="0"
        />
      </Field>
      <Field
        label="conda 环境"
        hint="留空=直跑;填了程序自动用 conda run -n <env> 包装(Windows 再加 cmd /c)"
        htmlFor="cmd-conda"
      >
        <TextInput
          id="cmd-conda"
          value={toStr(value.conda_env)}
          onChange={(e) => set("conda_env", fromStr(e.target.value))}
          placeholder="lmdeploy"
        />
      </Field>
      <Field label="命令行" hint="粘贴整条命令(可多行,换行当空格);含空格的路径用引号括起。程序拆成 argv 直跑(无 shell 特性:| > && $ 等不生效)">
        <TextArea
          value={line}
          onChange={(e) => onLine(e.target.value)}
          placeholder="lmdeploy serve /models/glm --model-name glm-4 --port 8000"
          rows={4}
        />
      </Field>

      {preview && (
        <div className="mb-3 rounded-md bg-muted px-3 py-2">
          <div className="mb-0.5 text-xs text-muted-foreground">将执行</div>
          <code className="block break-all font-mono text-xs text-foreground">{preview}</code>
        </div>
      )}

      <Button type="button" size="sm" variant="ghost" onClick={() => setShowAdvanced((v) => !v)}>
        {showAdvanced ? "▾ 高级(exe / args / cwd)" : "▸ 高级(exe / args / cwd)"}
      </Button>
      {showAdvanced && (
        <div className="mt-3 flex flex-col gap-3 border-t border-border pt-3">
          <Field label="exe(可执行文件)" htmlFor="cmd-exe">
            <TextInput id="cmd-exe" value={value.exe} onChange={(e) => set("exe", e.target.value)} />
          </Field>
          <Field label="args(参数,有序)" hint="每行一个;命令行解析歧义(如带空格路径)时在此手改">
            <StringListEditor
              values={value.args}
              onChange={(args) => set("args", args)}
              placeholder="--port 8000"
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
