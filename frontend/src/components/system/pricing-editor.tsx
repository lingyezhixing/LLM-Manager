import { Button } from "@/components/ui/button";
import { Field, NumberInput, Select, Switch } from "@/components/ui/form";
import type { Pricing, PricingTier } from "@/lib/api";

// 计费配置段:计费方式(按量/按时)+ 按时单价 / 阶梯表编辑器。空 tiers = 免费。
export function PricingEditor({ value, onChange }: { value: Pricing; onChange: (p: Pricing) => void }) {
  const setTier = (i: number, next: PricingTier) =>
    onChange({ ...value, tiers: value.tiers.map((t, idx) => (idx === i ? next : t)) });
  const removeTier = (i: number) =>
    onChange({ ...value, tiers: [...value.tiers.slice(0, i), ...value.tiers.slice(i + 1)] });
  const addTier = () =>
    onChange({
      ...value,
      tiers: [
        ...value.tiers,
        { tier_index: value.tiers.length + 1, min_input: 0, max_input: null,
          min_output: 0, max_output: null, input_price: 0, output_price: 0,
          support_cache: false, cache_write_price: 0, cache_read_price: 0 },
      ],
    });
  const num = (s: string): number | null => (s === "" ? null : Number(s));

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
          {value.tiers.map((t, i) => (
            <div key={i} className="rounded-md border border-border p-3">
              <div className="mb-2 flex items-center justify-between">
                <span className="text-xs font-medium text-muted-foreground">阶梯 {t.tier_index}</span>
                <Button type="button" size="sm" variant="ghost" className="text-destructive" onClick={() => removeTier(i)}>
                  删除
                </Button>
              </div>
              <div className="grid grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-4">
                <Field label="输入 min">
                  <NumberInput value={t.min_input ?? 0} onChange={(e) => setTier(i, { ...t, min_input: num(e.target.value) ?? 0 })} />
                </Field>
                <Field label="输入 max(空=∞)">
                  <NumberInput value={t.max_input ?? 0} onChange={(e) => setTier(i, { ...t, max_input: num(e.target.value) })} />
                </Field>
                <Field label="输出 min">
                  <NumberInput value={t.min_output ?? 0} onChange={(e) => setTier(i, { ...t, min_output: num(e.target.value) ?? 0 })} />
                </Field>
                <Field label="输出 max(空=∞)">
                  <NumberInput value={t.max_output ?? 0} onChange={(e) => setTier(i, { ...t, max_output: num(e.target.value) })} />
                </Field>
                <Field label="输入价(元/M)">
                  <NumberInput value={t.input_price} onChange={(e) => setTier(i, { ...t, input_price: e.target.value === "" ? 0 : Number(e.target.value) })} />
                </Field>
                <Field label="输出价(元/M)">
                  <NumberInput value={t.output_price} onChange={(e) => setTier(i, { ...t, output_price: e.target.value === "" ? 0 : Number(e.target.value) })} />
                </Field>
                <Field label="缓存读价(元/M)">
                  <NumberInput value={t.cache_read_price} disabled={!t.support_cache} onChange={(e) => setTier(i, { ...t, cache_read_price: e.target.value === "" ? 0 : Number(e.target.value) })} />
                </Field>
                <Field label="缓存写价(元/M)">
                  <NumberInput value={t.cache_write_price} disabled={!t.support_cache} onChange={(e) => setTier(i, { ...t, cache_write_price: e.target.value === "" ? 0 : Number(e.target.value) })} />
                </Field>
              </div>
              <div className="mt-2 flex items-center gap-2">
                <Switch checked={t.support_cache} onChange={(v) => setTier(i, { ...t, support_cache: v })} />
                <span className="text-xs text-muted-foreground">支持缓存(prompt_n 同算输入费 + 写缓存费)</span>
              </div>
            </div>
          ))}
          <Button type="button" size="sm" variant="ghost" onClick={addTier}>+ 添加阶梯</Button>
        </>
      )}
    </div>
  );
}
