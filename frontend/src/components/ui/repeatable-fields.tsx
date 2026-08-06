import type { KeyboardEvent } from "react";
import { Button } from "@/components/ui/button";
import { ComboboxInput } from "@/components/ui/combobox";
import { NumberInput, TextInput } from "@/components/ui/form";

// 可复用行编辑器原子(模型定义 CRUD 用;后续其它键值/列表字段可复用)。
// StringListEditor:有序字符串列表(args / required_devices / aliases)。
// KeyValueEditor:键=值(env str→str / memory_mb str→int)。
// 受控:父级持数组/对象,onChange 回传新值。全部不可变更新(无原地改)。

const removeBtn = "shrink-0 h-9 px-2 text-xs text-muted-foreground hover:text-destructive";

export function StringListEditor({
  values,
  onChange,
  placeholder,
}: {
  values: string[];
  onChange: (next: string[]) => void;
  placeholder?: string;
}) {
  const update = (i: number, v: string) =>
    onChange(values.map((x, idx) => (idx === i ? v : x)));
  // 用 slice 删(避免 filter 的未用形参,noUnusedParameters)。
  const remove = (i: number) => onChange([...values.slice(0, i), ...values.slice(i + 1)]);
  const add = () => onChange([...values, ""]);

  return (
    <div className="flex flex-col gap-2">
      {values.map((v, i) => (
        <div key={i} className="flex items-center gap-2">
          <TextInput
            value={v}
            placeholder={placeholder}
            onChange={(e) => update(i, e.target.value)}
            onKeyDown={(e: KeyboardEvent<HTMLInputElement>) => {
              if (e.key === "Enter") {
                e.preventDefault();
                add();
              }
            }}
          />
          <button type="button" className={removeBtn} onClick={() => remove(i)}>✕</button>
        </div>
      ))}
      <Button type="button" size="sm" variant="ghost" onClick={add}>+ 添加</Button>
    </div>
  );
}

export function KeyValueEditor({
  entries,
  onChange,
  valuePlaceholder,
  numeric,
  keyPlaceholder = "键",
  keyOptions,
  valueSuffix,
}: {
  entries: Record<string, string | number>;
  onChange: (next: Record<string, string | number>) => void;
  valuePlaceholder?: string;
  numeric?: boolean;
  keyPlaceholder?: string;
  // 键输入框的可选下拉建议(ComboboxInput:聚焦展开全部,可点选也可手输)。
  keyOptions?: string[];
  // 值输入框后的单位标注(如显存的 MB)。
  valueSuffix?: string;
}) {
  const pairs = Object.entries(entries);

  const setKey = (i: number, key: string) => {
    const [oldKey, val] = pairs[i];
    const rest = { ...entries };
    delete rest[oldKey];
    rest[key] = val;
    onChange(rest);
  };
  const setValue = (i: number, val: string | number) =>
    onChange({ ...entries, [pairs[i][0]]: val });
  const remove = (i: number) => {
    const rest = { ...entries };
    delete rest[pairs[i][0]];
    onChange(rest);
  };
  const add = () => onChange({ ...entries, "": numeric ? 0 : "" });

  return (
    <div className="flex flex-col gap-2">
      {pairs.map(([k, v], i) => (
        <div key={i} className="flex items-center gap-2">
          {keyOptions ? (
            <ComboboxInput value={k} options={keyOptions} onChange={(key) => setKey(i, key)} />
          ) : (
            <TextInput value={k} placeholder={keyPlaceholder} onChange={(e) => setKey(i, e.target.value)} />
          )}
          {numeric ? (
            <div className="flex min-w-0 flex-1 items-center gap-1">
              <NumberInput
                value={Number(v)}
                placeholder={valuePlaceholder}
                onChange={(e) => setValue(i, e.target.value === "" ? 0 : Number(e.target.value))}
              />
              {valueSuffix && <span className="shrink-0 text-xs text-muted-foreground">{valueSuffix}</span>}
            </div>
          ) : (
            <div className="flex min-w-0 flex-1 items-center gap-1">
              <TextInput
                value={String(v)}
                placeholder={valuePlaceholder}
                onChange={(e) => setValue(i, e.target.value)}
              />
            </div>
          )}
          <button type="button" className={removeBtn} onClick={() => remove(i)}>✕</button>
        </div>
      ))}
      <Button type="button" size="sm" variant="ghost" onClick={add}>+ 添加</Button>
    </div>
  );
}
