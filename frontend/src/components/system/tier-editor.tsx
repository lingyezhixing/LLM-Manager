import { Button } from "@/components/ui/button";
import { NumberInput } from "@/components/ui/form";
import { numOrNull as num } from "@/lib/format";
import type { CloudTier, PricingTier } from "@/lib/api";

// 共享阶梯表编辑器:本地模型计费(PricingTier)与云端模型计费(CloudTier)共用。
// 两类型同构(逐字段一致),union 表达让同一套编辑逻辑两边复用。

type Tier = PricingTier | CloudTier;

/** 阶梯单行紧凑输入:小 label + 数字框。flex-1 均分行内宽度——容器宽时 8 框一行,
 * 窄时 flex-wrap 自动换行且每行内仍均分填满(纯 CSS 自适应,无需 JS 测量)。 */
function TierInput({ label, value, onChange, disabled }: {
  label: string;
  value: number | string;
  onChange: (v: number | null) => void;
  disabled?: boolean;
}) {
  return (
    <div className="flex min-w-0 flex-1 flex-col gap-0.5">
      <span className="truncate text-micro leading-none text-muted-foreground">{label}</span>
      <NumberInput
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(num(e.target.value))}
        className="w-full"
      />
    </div>
  );
}

export function TierEditor({ tiers, supportCache, onChange }: {
  tiers: Tier[];
  supportCache: boolean;
  onChange: (next: Tier[]) => void;
}) {
  const setTier = (i: number, next: Tier) => onChange(tiers.map((t, idx) => (idx === i ? next : t)));
  const removeTier = (i: number) =>
    onChange(tiers.filter((_, idx) => idx !== i).map((t, idx) => ({ ...t, tier_index: idx + 1 })));
  const addTier = () =>
    onChange([
      ...tiers,
      { tier_index: Math.max(0, ...tiers.map((t) => t.tier_index)) + 1, min_input: 0, max_input: null,
        min_output: 0, max_output: null, input_price: 0, output_price: 0, cache_write_price: 0, cache_read_price: 0 },
    ]);
  return (
    <div className="flex flex-col gap-3">
      {tiers.map((t, i) => (
        <div key={i} className="flex flex-wrap items-end gap-x-3 gap-y-2 rounded-md border border-border px-3 py-2">
          <span className="self-center text-xs font-medium text-muted-foreground">阶梯 {t.tier_index}</span>
          <TierInput label="输入min" value={t.min_input ?? 0} onChange={(v) => setTier(i, { ...t, min_input: v ?? 0 })} />
          <TierInput label="输入max" value={t.max_input ?? ""} onChange={(v) => setTier(i, { ...t, max_input: v })} />
          <TierInput label="输出min" value={t.min_output ?? 0} onChange={(v) => setTier(i, { ...t, min_output: v ?? 0 })} />
          <TierInput label="输出max" value={t.max_output ?? ""} onChange={(v) => setTier(i, { ...t, max_output: v })} />
          <TierInput label="输入价" value={t.input_price} onChange={(v) => setTier(i, { ...t, input_price: v ?? 0 })} />
          <TierInput label="输出价" value={t.output_price} onChange={(v) => setTier(i, { ...t, output_price: v ?? 0 })} />
          <TierInput label="缓存读" value={t.cache_read_price} disabled={!supportCache}
            onChange={(v) => setTier(i, { ...t, cache_read_price: v ?? 0 })} />
          <TierInput label="缓存写" value={t.cache_write_price} disabled={!supportCache}
            onChange={(v) => setTier(i, { ...t, cache_write_price: v ?? 0 })} />
          <Button type="button" size="sm" variant="ghost" className="mb-0.5 text-destructive"
            onClick={() => removeTier(i)}>删除</Button>
        </div>
      ))}
      <Button type="button" size="sm" variant="ghost" onClick={addTier}>+ 添加阶梯</Button>
    </div>
  );
}
