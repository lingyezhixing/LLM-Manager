import { useQuery } from "@tanstack/react-query";
import { CommandEditor } from "@/components/system/command-editor";
import { Field, RemoveButton, TextInput } from "@/components/ui/form";
import { KeyValueEditor } from "@/components/ui/repeatable-fields";
import { isPendingKey } from "@/lib/pending-keys";
import { apiJson } from "@/lib/api/shared";
import type { DevicesResponse, SchemeDef } from "@/lib/api";
import { qk } from "@/lib/api/keys";

// 一个 scheme:config_source + command 块 + required_devices + memory_mb。
// 受控:父级持 SchemeDef[](本组件只管一个,index/onChange/onRemove 由父级驱动)。
export function SchemeEditor({
  value,
  index,
  onChange,
  onRemove,
  vars,
}: {
  value: SchemeDef;
  index: number;
  onChange: (next: SchemeDef) => void;
  onRemove: () => void;
  // 命令变量替换({{port}}/{{alias}}),透传 CommandEditor 预览
  vars?: Record<string, string>;
}) {
  const set = <K extends keyof SchemeDef>(k: K, v: SchemeDef[K]) =>
    onChange({ ...value, [k]: v });

  // 设备名下拉建议:一次性 GET /api/devices(快照,零采样开销);1 分钟 staleTime 缓存,
  // 设备名变化频率极低,不重复请求。
  const { data: deviceNames } = useQuery({
    queryKey: qk.deviceNames,
    queryFn: async () => {
      const res = await apiJson<DevicesResponse>("/api/devices");
      return res.data.map((d) => d.device_name);
    },
    staleTime: 60_000,
  });

  // 合并 required_devices ∪ memory_mb:每行 = 设备名 → 显存 MB;设备名即所需设备(须在线)。
  const deviceMem: Record<string, number> = {};
  for (const d of value.required_devices) deviceMem[d] = value.memory_mb[d] ?? 0;
  for (const [d, mb] of Object.entries(value.memory_mb)) if (!(d in deviceMem)) deviceMem[d] = mb;

  return (
    <div className="rounded-md border border-border p-3">
      <div className="mb-3 flex items-center justify-between">
        <span className="text-xs font-medium text-muted-foreground">方案 #{index + 1}</span>
        <RemoveButton label={`删除方案 ${index + 1}`} onClick={onRemove} />
      </div>
      <Field label="方案名" htmlFor={`sch-src-${index}`}>
        <TextInput
          id={`sch-src-${index}`}
          value={value.config_source}
          onChange={(e) => set("config_source", e.target.value)}
        />
      </Field>
      <div className="mb-1 text-xs font-medium text-muted-foreground">启动命令</div>
      <CommandEditor value={value.command} onChange={(command) => set("command", command)} vars={vars} />
      <Field label="设备与显存">
        <KeyValueEditor
          entries={deviceMem}
          onChange={(entries) =>
            onChange({
              ...value,
              memory_mb: entries as Record<string, number>,
              // 待填哨兵键不算已要求设备(用户尚未填写真实设备名)
              required_devices: Object.keys(entries).filter((k) => !isPendingKey(k)),
            })
          }
          numeric
          valueSuffix="MB"
          keyOptions={deviceNames}
        />
      </Field>
    </div>
  );
}
