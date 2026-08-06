import type { ReactNode } from "react";
import { Field, NumberInput } from "@/components/ui/form";
import { numFromStr as num } from "@/lib/format";
import type { LogRetention } from "@/lib/api";

// 日志保留规则:恒生效的两个参数(按时间保留 N 天 + 按条数保留 N 条,系统与模型日志同时适用)。
// 无开关——规则始终生效,只配置数值。值须 ≥1(后端 ge=1)。
// head:可选前置字段(与保留规则同排,通用页把「日志级别」并入同一行)。
export function LogRetentionEditor({ value, onChange, head }: { value: LogRetention; onChange: (v: LogRetention) => void; head?: ReactNode }) {
  const set = <K extends keyof LogRetention>(k: K, v: LogRetention[K]) => onChange({ ...value, [k]: v });
  return (
    <div className="grid grid-cols-1 gap-x-6 sm:grid-cols-3">
      {head}
      <Field label="保留天数" htmlFor="lr-days" error={value.days < 1 ? "保留天数须 ≥ 1" : null}>
        <div className="flex items-center gap-2">
          <NumberInput id="lr-days" value={value.days} onChange={(e) => set("days", num(e.target.value))} className="w-24" />
          <span className="text-xs text-muted-foreground">天</span>
        </div>
      </Field>
      <Field label="保留条数" htmlFor="lr-count" error={value.count < 1 ? "保留条数须 ≥ 1" : null}>
        <div className="flex items-center gap-2">
          <NumberInput id="lr-count" value={value.count} onChange={(e) => set("count", num(e.target.value))} className="w-24" />
          <span className="text-xs text-muted-foreground">条</span>
        </div>
      </Field>
    </div>
  );
}
