import { Field, NumberInput, Select, Switch } from "@/components/ui/form";
import type { Pricing } from "@/lib/api";
import { TierEditor } from "@/components/system/tier-editor";

// 计费配置段:计费方式(按量/按时)+ 按时单价 / 阶梯表编辑器(共享 TierEditor)。空 tiers = 免费。
export function PricingEditor({ value, onChange }: { value: Pricing; onChange: (p: Pricing) => void }) {
  return (
    <div className="flex flex-col gap-3">
      {/* 计费方式 3 列 + 支持缓存/单价 1 列(按时计费时该格换为单价输入) */}
      <div className="grid grid-cols-1 gap-x-6 sm:grid-cols-4">
        <Field className="sm:col-span-3" label="计费方式" htmlFor="pr-type">
          <Select
            id="pr-type"
            value={value.pricing_type}
            onChange={(e) => onChange({ ...value, pricing_type: e.target.value as Pricing["pricing_type"] })}
          >
            <option value="tier">按量(分级,元/百万 token)</option>
            <option value="hourly">按时(元/小时)</option>
          </Select>
        </Field>
        {value.pricing_type === "hourly" ? (
          <Field label="单价(元/小时)" htmlFor="pr-hourly">
            <NumberInput
              id="pr-hourly"
              value={value.hourly_price}
              onChange={(e) => onChange({ ...value, hourly_price: e.target.value === "" ? 0 : Number(e.target.value) })}
            />
          </Field>
        ) : (
          <Field label="支持缓存" htmlFor="pr-cache">
            <div className="flex h-9 items-center">
              <Switch id="pr-cache" checked={value.support_cache} onChange={(v) => onChange({ ...value, support_cache: v })} />
            </div>
          </Field>
        )}
      </div>

      {value.pricing_type === "tier" && (
        <TierEditor
          tiers={value.tiers}
          supportCache={value.support_cache}
          onChange={(next) => onChange({ ...value, tiers: next })}
        />
      )}
    </div>
  );
}
