import { Field, NumberInput } from "@/components/ui/form";
import type { LogRetention } from "@/lib/api";

// 日志保留规则:恒生效的两个参数(按时间保留 N 天 + 按条数保留 N 条,系统与模型日志同时适用)。
// 无开关——规则始终生效,只配置数值。值须 ≥1(后端 ge=1)。
export function LogRetentionEditor({ value, onChange }: { value: LogRetention; onChange: (v: LogRetention) => void }) {
  const set = <K extends keyof LogRetention>(k: K, v: LogRetention[K]) => onChange({ ...value, [k]: v });
  const num = (s: string): number => (s === "" ? 0 : Number(s));
  return (
    <div className="grid grid-cols-1 gap-x-6 sm:grid-cols-2">
      <Field label="按时间清理" hint="保留最近 N 天日志(系统与模型日志同时适用)" htmlFor="lr-days" error={value.days < 1 ? "保留天数须 ≥ 1" : null}>
        <div className="flex items-center gap-2">
          <NumberInput id="lr-days" value={value.days} onChange={(e) => set("days", num(e.target.value))} className="w-24" />
          <span className="text-xs text-muted-foreground">天</span>
        </div>
      </Field>
      <Field label="按条数清理" hint="保留最近 N 条日志(系统与模型日志同时适用)" htmlFor="lr-count" error={value.count < 1 ? "保留条数须 ≥ 1" : null}>
        <div className="flex items-center gap-2">
          <NumberInput id="lr-count" value={value.count} onChange={(e) => set("count", num(e.target.value))} className="w-24" />
          <span className="text-xs text-muted-foreground">条</span>
        </div>
      </Field>
    </div>
  );
}
