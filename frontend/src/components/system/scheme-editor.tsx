import { Button } from "@/components/ui/button";
import { CommandEditor } from "@/components/system/command-editor";
import { Field, TextInput } from "@/components/ui/form";
import { KeyValueEditor, StringListEditor } from "@/components/ui/repeatable-fields";
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

  return (
    <div className="rounded-lg border border-border p-3">
      <div className="mb-3 flex items-center justify-between">
        <span className="text-xs font-medium text-muted-foreground">方案 #{index + 1}</span>
        <Button type="button" size="sm" variant="ghost" onClick={onRemove}>删除方案</Button>
      </div>
      <Field label="config_source(方案标识)" htmlFor={`sch-src-${index}`}>
        <TextInput
          id={`sch-src-${index}`}
          value={value.config_source}
          onChange={(e) => set("config_source", e.target.value)}
        />
      </Field>
      <div className="mb-2 text-xs font-medium text-muted-foreground">command</div>
      <CommandEditor value={value.command} onChange={(command) => set("command", command)} />
      <Field label="required_devices(所需设备)" hint="设备名自由填,后端归一化(小写+去空格)">
        <StringListEditor
          values={value.required_devices}
          onChange={(required_devices) => set("required_devices", required_devices)}
          placeholder="gpu0"
        />
      </Field>
      <Field label="memory_mb(每设备显存 MB)" hint="键 = 设备名,值 = MB">
        <KeyValueEditor
          entries={value.memory_mb}
          onChange={(memory_mb) => set("memory_mb", memory_mb as Record<string, number>)}
          valuePlaceholder="4096"
          numeric
        />
      </Field>
    </div>
  );
}
