import { Button } from "@/components/ui/button";
import { CommandEditor } from "@/components/system/command-editor";
import { Field, TextInput } from "@/components/ui/form";
import { KeyValueEditor } from "@/components/ui/repeatable-fields";
import type { SchemeDef } from "@/lib/api";

// 一个 scheme:config_source + command 块 + required_devices + memory_mb。
// 受控:父级持 SchemeDef[](本组件只管一个,index/onChange/onRemove 由父级驱动)。
export function SchemeEditor({
  value,
  index,
  onChange,
  onRemove,
}: {
  value: SchemeDef;
  index: number;
  onChange: (next: SchemeDef) => void;
  onRemove: () => void;
}) {
  const set = <K extends keyof SchemeDef>(k: K, v: SchemeDef[K]) =>
    onChange({ ...value, [k]: v });

  // 合并 required_devices ∪ memory_mb:每行 = 设备名 → 显存 MB;设备名即所需设备(须在线)。
  const deviceMem: Record<string, number> = {};
  for (const d of value.required_devices) deviceMem[d] = value.memory_mb[d] ?? 0;
  for (const [d, mb] of Object.entries(value.memory_mb)) if (!(d in deviceMem)) deviceMem[d] = mb;

  return (
    <div className="rounded-lg border border-border p-3">
      <div className="mb-3 flex items-center justify-between">
        <span className="text-xs font-medium text-muted-foreground">方案 #{index + 1}</span>
        <Button type="button" size="sm" variant="ghost" onClick={onRemove}>删除方案</Button>
      </div>
      <Field label="方案标识" hint="仅作标识,如 default / gpu0" htmlFor={`sch-src-${index}`}>
        <TextInput
          id={`sch-src-${index}`}
          value={value.config_source}
          onChange={(e) => set("config_source", e.target.value)}
        />
      </Field>
      <div className="mb-1 text-xs font-medium text-muted-foreground">启动命令</div>
      <CommandEditor value={value.command} onChange={(command) => set("command", command)} />
      <Field label="设备与显存(设备名 = MB)" hint="每行一个设备;设备名=所需设备(须在线),值=显存预算 MB。后端归一化(小写+去空格)">
        <KeyValueEditor
          entries={deviceMem}
          onChange={(entries) =>
            onChange({
              ...value,
              memory_mb: entries as Record<string, number>,
              required_devices: Object.keys(entries),
            })
          }
          numeric
          keyPlaceholder="gpu0"
          valuePlaceholder="4096"
        />
      </Field>
    </div>
  );
}
