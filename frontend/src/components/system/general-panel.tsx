import { type ReactNode, useEffect, useRef, useState } from "react";
import { ConfigSaveBar } from "@/components/config-save-bar";
import { Button } from "@/components/ui/button";
import { Field, NumberInput, Select, TextInput } from "@/components/ui/form";
import { useToast } from "@/components/ui/toast";
import { type ProgramConfig } from "@/lib/api";
import { useConfig, useUpdateProgram } from "@/lib/use-config";

const LOG_LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"];

function shallowEqual(a: ProgramConfig, b: ProgramConfig): boolean {
  return (Object.keys(a) as (keyof ProgramConfig)[]).every((k) => a[k] === b[k]);
}

// 段标题:轻量横线分隔(── 标题 ──────),与模型表单的分区一致。
function SectionTitle({ children }: { children: ReactNode }) {
  return (
    <div className="mb-3 mt-6 flex items-center gap-3">
      <span className="text-xs font-medium text-muted-foreground">{children}</span>
      <div className="h-px flex-1 bg-border" />
    </div>
  );
}

// 双列栅格容器:窄屏单列、≥sm 双列。Field 自带 mb-4 作行间距,故只设列间距。
function FieldGrid({ children }: { children: ReactNode }) {
  return <div className="grid grid-cols-1 gap-x-6 sm:grid-cols-2">{children}</div>;
}

export function GeneralPanel() {
  const { data, isLoading, isError, error, refetch } = useConfig();
  const update = useUpdateProgram();
  const toast = useToast();
  const [form, setForm] = useState<ProgramConfig | null>(data?.program ?? null);
  // 上一次采纳进表单的服务端值;区分「未编辑(form 仍 == 该值)→ 跟随外部刷新」vs「编辑中 → 保留」。
  const syncedRef = useRef<ProgramConfig | null>(data?.program ?? null);

  // 初值就绪填表单;后续 data 外部刷新时,若用户未编辑则跟随(避免 stale-form),编辑中则保留。
  // 用函数式更新读最新 form,避免把 form 列入依赖(编辑中不打断)。
  useEffect(() => {
    const incoming = data?.program;
    if (!incoming) return;
    setForm((prev) => {
      const base = syncedRef.current;
      if (prev !== null && base !== null && !shallowEqual(prev, base)) return prev; // 编辑中,保留
      syncedRef.current = incoming;
      return incoming;
    });
  }, [data?.program]);

  if (isError) {
    return (
      <div className="flex items-center gap-2 text-sm text-destructive">
        加载失败:{(error as Error).message}
        <Button size="sm" variant="ghost" onClick={() => refetch()}>重试</Button>
      </div>
    );
  }
  if (isLoading || !form) {
    return <div className="text-sm text-muted-foreground">加载中…</div>;
  }

  const initial = data!.program;
  const dirty = !shallowEqual(form, initial);
  const portValid = form.port >= 1 && form.port <= 65535;
  const aliveValid = form.alive_time >= 0;
  const set = <K extends keyof ProgramConfig>(k: K, v: ProgramConfig[K]) =>
    setForm({ ...form, [k]: v });
  const num = (s: string): number => (s === "" ? 0 : Number(s));

  return (
    <div>
      {/* 重启策略说明:取代原先每个字段重复挂的「🔴 需重启」。改完需重启时顶部横幅会提示。 */}
      <p className="mb-1 text-xs text-muted-foreground">
        host / port / 日志 / 路径类字段改完需重启程序(顶部会提示);alive_time 即时生效。
      </p>

      <SectionTitle>监听与运行</SectionTitle>
      <FieldGrid>
        <Field label="监听地址 (host)" htmlFor="cfg-host">
          <TextInput id="cfg-host" value={form.host} onChange={(e) => set("host", e.target.value)} />
        </Field>
        <Field label="监听端口 (port)" htmlFor="cfg-port"
          error={!portValid && form.port !== 0 ? "端口须在 1–65535" : null}>
          <NumberInput id="cfg-port" value={form.port} onChange={(e) => set("port", num(e.target.value))} />
        </Field>
        <Field label="空闲检测 (alive_time)" hint="秒 · 🟢 改完即时生效" htmlFor="cfg-alive"
          error={!aliveValid ? "须 ≥ 0" : null}>
          <NumberInput id="cfg-alive" value={form.alive_time} onChange={(e) => set("alive_time", num(e.target.value))} />
        </Field>
      </FieldGrid>

      <SectionTitle>日志</SectionTitle>
      <FieldGrid>
        <Field label="日志级别 (log_level)" htmlFor="cfg-level">
          <Select id="cfg-level" value={form.log_level} onChange={(e) => set("log_level", e.target.value)}>
            {LOG_LEVELS.map((lv) => (
              <option key={lv} value={lv}>{lv}</option>
            ))}
          </Select>
        </Field>
        <Field label="日志目录 (log_dir)" htmlFor="cfg-logdir">
          <TextInput id="cfg-logdir" value={form.log_dir} onChange={(e) => set("log_dir", e.target.value)} />
        </Field>
      </FieldGrid>

      <SectionTitle>数据与集成</SectionTitle>
      <FieldGrid>
        <Field label="数据库路径 (db_path)" hint="改完需手动迁移文件" htmlFor="cfg-db">
          <TextInput id="cfg-db" value={form.db_path} onChange={(e) => set("db_path", e.target.value)} />
        </Field>
        <Field label="Claude settings 路径" htmlFor="cfg-claude">
          <TextInput id="cfg-claude" value={form.claude_settings_path} onChange={(e) => set("claude_settings_path", e.target.value)} />
        </Field>
      </FieldGrid>

      {dirty && (
        <ConfigSaveBar
          saving={update.isPending}
          error={update.error ? (update.error as Error).message : null}
          onSave={() =>
            update.mutate(form, {
              onSuccess: () => {
                syncedRef.current = form;
                toast.success("系统配置已保存");
              },
            })
          }
          onReset={() => setForm(initial)}
        />
      )}
    </div>
  );
}
