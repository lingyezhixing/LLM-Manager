import { Button } from "@/components/ui/button";
import { Field, NumberInput, Select, Switch } from "@/components/ui/form";
import { numOrNull as num } from "@/lib/format";
import type { Pricing, PricingTier } from "@/lib/api";

/** 阶梯单行紧凑输入:小 label + 窄数字框(多个阶梯字段排一行)。 */
function TierInput({ label, value, onChange, disabled }: {
  label: string;
  value: number | string;
  onChange: (v: number | null) => void;
  disabled?: boolean;
}) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-[10px] leading-none text-muted-foreground">{label}</span>
      <NumberInput
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(num(e.target.value))}
        className="w-16"
      />
    </div>
  );
}

// 计费配置段:计费方式(按量/按时)+ 按时单价 / 阶梯表编辑器。空 tiers = 免费。
export function PricingEditor({ value, onChange }: { value: Pricing; onChange: (p: Pricing) => void }) {
  const setTier = (i: number, next: PricingTier) =>
    onChange({ ...value, tiers: value.tiers.map((t, idx) => (idx === i ? next : t)) });
  const removeTier = (i: number) =>
    onChange({
      ...value,
      tiers: value.tiers
        .filter((_, idx) => idx !== i)
        .map((t, idx) => ({ ...t, tier_index: idx + 1 })),
    });
  const addTier = () =>
    onChange({
      ...value,
      tiers: [
        ...value.tiers,
        { tier_index: Math.max(0, ...value.tiers.map((t) => t.tier_index)) + 1, min_input: 0, max_input: null,
          min_output: 0, max_output: null, input_price: 0, output_price: 0,
          cache_write_price: 0, cache_read_price: 0 },
      ],
    });

  return (
    <div className="flex flex-col gap-3">
      <Field label="计费方式" htmlFor="pr-type">
        <Select
          id="pr-type"
          value={value.pricing_type}
          onChange={(e) => onChange({ ...value, pricing_type: e.target.value as Pricing["pricing_type"] })}
        >
          <option value="tier">按量(分级,元/百万 token)</option>
          <option value="hourly">按时(元/小时)</option>
        </Select>
      </Field>

      {value.pricing_type === "hourly" && (
        <Field label="单价(元/小时)" htmlFor="pr-hourly">
          <NumberInput
            id="pr-hourly"
            value={value.hourly_price}
            onChange={(e) => onChange({ ...value, hourly_price: e.target.value === "" ? 0 : Number(e.target.value) })}
          />
        </Field>
      )}

      {value.pricing_type === "tier" && (
        <>
          <p className="text-xs text-muted-foreground">
            每个阶梯 = 一个 token 量区间 + 单价;请求命中首个匹配阶梯。留空阶梯 = 免费。min=0 闭区间,否则开区间;max 留空 = 无上限。
          </p>
          <div className="flex items-center gap-2">
            <Switch id="pr-cache" checked={value.support_cache} onChange={(v) => onChange({ ...value, support_cache: v })} />
            <label htmlFor="pr-cache" className="text-xs text-muted-foreground">支持缓存(prompt_n 同算输入费 + 写缓存费)</label>
          </div>
          {value.tiers.map((t, i) => (
            <div key={i} className="flex flex-wrap items-end gap-x-3 gap-y-2 rounded-md border border-border px-3 py-2">
              <span className="mb-0.5 text-xs font-medium text-muted-foreground">阶梯 {t.tier_index}</span>
              <TierInput label="输入min" value={t.min_input ?? 0}
                onChange={(v) => setTier(i, { ...t, min_input: v ?? 0 })} />
              <TierInput label="输入max" value={t.max_input ?? ""}
                onChange={(v) => setTier(i, { ...t, max_input: v })} />
              <TierInput label="输出min" value={t.min_output ?? 0}
                onChange={(v) => setTier(i, { ...t, min_output: v ?? 0 })} />
              <TierInput label="输出max" value={t.max_output ?? ""}
                onChange={(v) => setTier(i, { ...t, max_output: v })} />
              <TierInput label="输入价" value={t.input_price}
                onChange={(v) => setTier(i, { ...t, input_price: v ?? 0 })} />
              <TierInput label="输出价" value={t.output_price}
                onChange={(v) => setTier(i, { ...t, output_price: v ?? 0 })} />
              <TierInput label="缓存读" value={t.cache_read_price} disabled={!value.support_cache}
                onChange={(v) => setTier(i, { ...t, cache_read_price: v ?? 0 })} />
              <TierInput label="缓存写" value={t.cache_write_price} disabled={!value.support_cache}
                onChange={(v) => setTier(i, { ...t, cache_write_price: v ?? 0 })} />
              <Button type="button" size="sm" variant="ghost" className="mb-0.5 text-destructive"
                onClick={() => removeTier(i)}>
                删除
              </Button>
            </div>
          ))}
          <Button type="button" size="sm" variant="ghost" onClick={addTier}>+ 添加阶梯</Button>
        </>
      )}
    </div>
  );
}
