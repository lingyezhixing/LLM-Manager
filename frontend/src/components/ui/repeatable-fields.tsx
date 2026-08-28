import type { KeyboardEvent } from "react";
import { Button } from "@/components/ui/button";
import { ComboboxInput } from "@/components/ui/combobox";
import { NumberInput, RemoveButton, TextInput } from "@/components/ui/form";
import { isPendingKey, PENDING_KEY_PREFIX } from "@/lib/pending-keys";

// 可复用行编辑器原子(模型定义 CRUD 用)。
// StringListEditor:有序字符串列表(args / required_devices / aliases)。
// KeyValueEditor:键=值(env str→str / memory_mb str→int)。
// 受控:父级持数组/对象,onChange 回传新值。全部不可变更新(无原地改)。
// 行 key 用 index:行完全受控(value 全由 props 驱动,无内部 state),index key 下删除
// 中间行显示仍正确(React 复用节点但 value 随 props 更新),仅焦点可能错位——低风险已知
// 折衷;引入稳定行 id 会污染父级数据模型(需后端类型同步),收益不足,维持现状。

let pendingSeq = 0;

function newPendingKey(): string {
  return `${PENDING_KEY_PREFIX}${pendingSeq++}`;
}

export function StringListEditor({
  values,
  onChange,
}: {
  values: string[];
  onChange: (next: string[]) => void;
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
            onChange={(e) => update(i, e.target.value)}
            onKeyDown={(e: KeyboardEvent<HTMLInputElement>) => {
              if (e.key === "Enter") {
                e.preventDefault();
                add();
              }
            }}
          />
          <RemoveButton label="删除此项" onClick={() => remove(i)} />
        </div>
      ))}
      <Button type="button" size="sm" variant="ghost" onClick={add}>+ 添加</Button>
    </div>
  );
}

export function KeyValueEditor({
  entries,
  onChange,
  numeric,
  keyOptions,
  valueSuffix,
}: {
  entries: Record<string, string | number>;
  onChange: (next: Record<string, string | number>) => void;
  numeric?: boolean;
  // 键输入框的可选下拉建议(ComboboxInput:聚焦展开全部,可点选也可手输)。
  keyOptions?: string[];
  // 值输入框后的单位标注(如显存的 MB)。
  valueSuffix?: string;
}) {
  const pairs = Object.entries(entries);

  const setKey = (i: number, key: string) => {
    const [oldKey] = pairs[i];
    // 撞键守卫:目标键已是其它行的键(且非待填哨兵)→ 拒绝,防静默覆盖丢值。
    if (
      key !== "" && key !== oldKey && !isPendingKey(key)
      && pairs.some(([k], j) => j !== i && k === key)
    ) return;
    // 原位改名保持插入序:遍历原键序,oldKey 处换成新键(删除+重插会移到末尾,导致行跳动)。
    const rest: Record<string, string | number> = {};
    for (const [k, v] of Object.entries(entries)) {
      rest[k === oldKey ? key : k] = v;
    }
    onChange(rest);
  };
  const setValue = (i: number, val: string | number) =>
    onChange({ ...entries, [pairs[i][0]]: val });
  const remove = (i: number) => {
    const rest = { ...entries };
    delete rest[pairs[i][0]];
    onChange(rest);
  };
  // 空行用唯一哨兵键(非 ""):Record 模型下多个待填行可共存(用 "" 会互相覆盖)。
  const add = () => onChange({ ...entries, [newPendingKey()]: numeric ? 0 : "" });

  return (
    <div className="flex flex-col gap-2">
      {pairs.map(([k, v], i) => (
        <div key={i} className="flex items-center gap-2">
          {keyOptions ? (
            <ComboboxInput
              value={isPendingKey(k) ? "" : k}
              options={keyOptions}
              onChange={(key) => setKey(i, key)}
            />
          ) : (
            // 键框限 flex-1:裸 w-full 以 100% 基准占满行,把值框挤到 0 宽
            <TextInput
              className="min-w-0 flex-1"
              value={isPendingKey(k) ? "" : k}
              onChange={(e) => setKey(i, e.target.value)}
            />
          )}
          {numeric ? (
            <div className="flex min-w-0 flex-1 items-center gap-1">
              <NumberInput
                value={Number(v)}
                onChange={(e) => setValue(i, e.target.value === "" ? 0 : Number(e.target.value))}
              />
              {valueSuffix && <span className="shrink-0 text-xs text-muted-foreground">{valueSuffix}</span>}
            </div>
          ) : (
            <div className="flex min-w-0 flex-1 items-center gap-1">
              <TextInput
                value={String(v)}
                onChange={(e) => setValue(i, e.target.value)}
              />
            </div>
          )}
          <RemoveButton label="删除此项" onClick={() => remove(i)} />
        </div>
      ))}
      <Button type="button" size="sm" variant="ghost" onClick={add}>+ 添加</Button>
    </div>
  );
}
