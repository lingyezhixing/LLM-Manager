import { type ReactNode, useEffect, useRef, useState } from "react";
import { ConfigSaveBar } from "@/components/config-save-bar";
import { ErrorState } from "@/components/ui/error-state";
import { Field, NumberInput, Select, TextInput } from "@/components/ui/form";
import { numFromStr as num } from "@/lib/format";
import { InfoTile } from "@/components/ui/info-tile";
import { useToast } from "@/lib/hooks/use-toast";
import { LogRetentionEditor } from "@/components/system/log-retention-editor";
import { type LogRetention, type ProgramConfig } from "@/lib/api";
import { useConfig, useUpdateLogRetention, useUpdateProgram } from "@/lib/hooks/use-config";
import { useNowTick } from "@/lib/hooks/use-now-tick";
import { useSystemInfo } from "@/lib/hooks/use-config";

const LOG_LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"];

function formatDuration(sec: number): string {
  const s = Math.max(0, Math.floor(sec));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const ss = s % 60;
  const pad = (x: number) => String(x).padStart(2, "0");
  return `${pad(h)}:${pad(m)}:${pad(ss)}`;
}

function sameProgram(a: ProgramConfig, b: ProgramConfig): boolean {
  return (Object.keys(a) as (keyof ProgramConfig)[]).every((k) => a[k] === b[k]);
}

function sameLogs(a: LogRetention, b: LogRetention): boolean {
  return a.days === b.days && a.count === b.count;
}

// 通用页表单 = program 段 + 日志保留段;两段各自独立 dirty,保存时分别 PUT 对应端点。
interface GeneralForm {
  program: ProgramConfig;
  logs: LogRetention;
}
const sameForm = (a: GeneralForm, b: GeneralForm) => sameProgram(a.program, b.program) && sameLogs(a.logs, b.logs);

// 段标题:轻量横线分隔(── 标题 ──────),与模型表单的分区一致。
function SectionTitle({ children }: { children: ReactNode }) {
  return (
    <div className="mb-3 mt-6 flex items-center gap-3">
      <span className="text-xs font-medium text-muted-foreground">{children}</span>
      <div className="h-px flex-1 bg-border" />
    </div>
  );
}

// 三列栅格容器:窄屏单列、≥sm 三列(监听三项/日志三项各排一行)。
// Field 自带 mb-4 作行间距,故只设列间距。
function FieldGrid({ children }: { children: ReactNode }) {
  return <div className="grid grid-cols-1 gap-x-6 sm:grid-cols-3">{children}</div>;
}

export function GeneralPanel() {
  const { data, isLoading, isError, error, refetch } = useConfig();
  const { data: info } = useSystemInfo();
  const now = useNowTick(1000);
  const update = useUpdateProgram();
  const updateLogs = useUpdateLogRetention();
  const toast = useToast();
  const [form, setForm] = useState<GeneralForm | null>(null);
  // 上一次采纳进表单的服务端值;区分「未编辑(form 仍 == 该值)→ 跟随外部刷新」vs「编辑中 → 保留」。
  const syncedRef = useRef<GeneralForm | null>(null);

  // 初值就绪填表单;后续 data 外部刷新时,若用户未编辑则跟随(避免 stale-form),编辑中则保留。
  // 用函数式更新读最新 form,避免把 form 列入依赖(编辑中不打断)。
  useEffect(() => {
    if (!data) return;
    const incoming: GeneralForm = { program: data.program, logs: data.logs };
    setForm((prev) => {
      const base = syncedRef.current;
      if (prev !== null && base !== null && !sameForm(prev, base)) return prev; // 编辑中,保留
      syncedRef.current = incoming;
      return incoming;
    });
  }, [data]);

  if (isError) {
    return <ErrorState message={(error as Error).message} onRetry={() => refetch()} />;
  }
  if (isLoading || !form) {
    return <div className="text-sm text-muted-foreground">加载中…</div>;
  }

  const initial: GeneralForm = { program: data!.program, logs: data!.logs };
  // dirty 以 syncedRef(最近采纳的服务端值)为基准,而非 live data——外部刷新被中途丢弃后,
  // 若用户恰好还原到 syncedRef,保存条应熄灭而非出现「点了没反应」的幽灵态。
  const dirty = syncedRef.current !== null && !sameForm(form, syncedRef.current);
  const portValid = form.program.port >= 1 && form.program.port <= 65535;
  const aliveValid = form.program.alive_time >= 0;
  const set = (p: ProgramConfig) => setForm({ ...form, program: p });
  const setLogs = (l: LogRetention) => setForm({ ...form, logs: l });
  const saving = update.isPending || updateLogs.isPending;
  const saveError = update.error ?? updateLogs.error;

  const onSave = () => {
    const pDirty = !sameProgram(form.program, syncedRef.current!.program);
    const lDirty = !sameLogs(form.logs, syncedRef.current!.logs);
    let toasted = false;
    if (pDirty) {
      update.mutate(form.program, {
        onSuccess: () => {
          syncedRef.current = { ...syncedRef.current!, program: form.program };
          if (!toasted) { toasted = true; toast.success("系统配置已保存"); }
        },
      });
    }
    if (lDirty) {
      updateLogs.mutate(form.logs, {
        onSuccess: () => {
          syncedRef.current = { ...syncedRef.current!, logs: form.logs };
          if (!toasted) { toasted = true; toast.success("系统配置已保存"); }
        },
      });
    }
  };

  return (
    <div>
      {info && (
        <div className="mb-4 grid grid-cols-2 gap-2 sm:grid-cols-3">
          <InfoTile label="版本" value={info.version} valueClass="break-all text-foreground" />
          <InfoTile label="启动时间" value={new Date(info.started_at * 1000).toLocaleString()} valueClass="break-all text-foreground" />
          <InfoTile label="运行时长" value={formatDuration(now / 1000 - info.started_at)} valueClass="break-all text-foreground" />
        </div>
      )}

      <SectionTitle>监听与运行</SectionTitle>
      <FieldGrid>
        <Field label="监听地址" htmlFor="cfg-host">
          <TextInput id="cfg-host" value={form.program.host} onChange={(e) => set({ ...form.program, host: e.target.value })} />
        </Field>
        <Field label="监听端口" htmlFor="cfg-port"
          error={!portValid && form.program.port !== 0 ? "端口须在 1–65535" : null}>
          <NumberInput id="cfg-port" value={form.program.port} onChange={(e) => set({ ...form.program, port: num(e.target.value) })} />
        </Field>
        <Field label="空闲检测 (分钟)" htmlFor="cfg-alive"
          error={!aliveValid ? "须 ≥ 0" : null}>
          <NumberInput id="cfg-alive" value={form.program.alive_time} onChange={(e) => set({ ...form.program, alive_time: num(e.target.value) })} />
        </Field>
      </FieldGrid>

      <SectionTitle>日志</SectionTitle>
      <LogRetentionEditor
        value={form.logs}
        onChange={setLogs}
        head={
          <Field label="日志级别" htmlFor="cfg-level">
            <Select id="cfg-level" value={form.program.log_level} onChange={(e) => set({ ...form.program, log_level: e.target.value })}>
              {LOG_LEVELS.map((lv) => (
                <option key={lv} value={lv}>{lv}</option>
              ))}
            </Select>
          </Field>
        }
      />

      {dirty && (
        <ConfigSaveBar
          saving={saving}
          error={saveError ? (saveError as Error).message : null}
          onSave={onSave}
          onReset={() => {
            syncedRef.current = initial;
            setForm(initial);
          }}
          saveDisabled={!portValid || !aliveValid || form.logs.days < 1 || form.logs.count < 1}
        />
      )}
    </div>
  );
}
