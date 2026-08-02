import { Field, NumberInput, Switch } from "@/components/ui/form";
import type { LogRetention } from "@/lib/api";

// 日志保留规则:时间清理 + 条数清理两组开关+数字。开关关时数字禁用;值须 ≥1(后端 ge=1)。
export function LogRetentionEditor({ value, onChange }: { value: LogRetention; onChange: (v: LogRetention) => void }) {
  const set = <K extends keyof LogRetention>(k: K, v: LogRetention[K]) => onChange({ ...value, [k]: v });
  const num = (s: string): number => (s === "" ? 0 : Number(s));
  const timeOk = !value.time_enabled || value.days >= 1;
  const countOk = !value.count_enabled || value.count >= 1;
  return (
    <div className="grid grid-cols-1 gap-x-6 sm:grid-cols-2">
      <Field label="按时间清理" hint="保留最近 N 天日志(系统与模型日志同时适用)" htmlFor="lr-time" error={!timeOk ? "保留天数须 ≥ 1" : null}>
        <div className="flex items-center gap-2">
          <Switch id="lr-time" checked={value.time_enabled} onChange={(v) => set("time_enabled", v)} />
          <NumberInput value={value.days} disabled={!value.time_enabled} onChange={(e) => set("days", num(e.target.value))} className="w-24" />
          <span className="text-xs text-muted-foreground">天</span>
        </div>
      </Field>
      <Field label="按条数清理" hint="保留最近 N 条日志(系统与模型日志同时适用)" htmlFor="lr-count" error={!countOk ? "保留条数须 ≥ 1" : null}>
        <div className="flex items-center gap-2">
          <Switch id="lr-count" checked={value.count_enabled} onChange={(v) => set("count_enabled", v)} />
          <NumberInput value={value.count} disabled={!value.count_enabled} onChange={(e) => set("count", num(e.target.value))} className="w-24" />
          <span className="text-xs text-muted-foreground">条</span>
        </div>
      </Field>
    </div>
  );
}
