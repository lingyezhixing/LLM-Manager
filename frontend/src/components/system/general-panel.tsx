import { type ReactNode, useMemo } from "react";
import { ConfigSaveBar } from "@/components/config-save-bar";
import { Loading } from "@/components/ui/card";
import { ErrorState } from "@/components/ui/error-state";
import { Field, NumberInput, Select, TextInput } from "@/components/ui/form";
import { errMsg, formatClock, numFromStr as num, portError } from "@/lib/format";
import { InfoTile } from "@/components/ui/info-tile";
import { useToast } from "@/lib/hooks/use-toast";
import { LogRetentionEditor } from "@/components/system/log-retention-editor";
import { UpdatePanel } from "@/components/system/update-panel";
import { type LogRetention, type ProgramConfig } from "@/lib/api";
import {
  useConfig,
  useSystemInfo,
  useUpdateLogRetention,
  useUpdateProgram,
} from "@/lib/hooks/use-config";
import { useNowTick } from "@/lib/hooks/use-now-tick";
import { useSyncedForm } from "@/lib/hooks/use-synced-form";

const LOG_LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"];

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
// useSyncedForm 的 null 感知比较(初始/未加载态 form=baseline=null)。
const sameFormOrNull = (a: GeneralForm | null, b: GeneralForm | null) =>
  a === b || (a !== null && b !== null && sameForm(a, b));

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
  const serverForm = useMemo<GeneralForm | null>(
    () => (data ? { program: data.program, logs: data.logs } : null),
    [data],
  );
  const { form, setForm, dirty, baseline, advance, commit } = useSyncedForm<GeneralForm | null>(
    serverForm,
    null,
    sameFormOrNull,
  );

  if (isError) {
    return <ErrorState message={errMsg(error)} onRetry={() => refetch()} />;
  }
  if (isLoading || !form) {
    return <Loading />;
  }

  // M7:form 非空 ⇒ data 已就绪(form 只在 data 就绪后填充);此处兜底防 data 中途变 undefined
  const initial: GeneralForm = data
    ? { program: data.program, logs: data.logs }
    : { program: form.program, logs: form.logs };
  const portValid = portError(form.program.port) === null;
  const aliveValid = form.program.alive_time >= 0;
  const set = (p: ProgramConfig) => setForm({ ...form, program: p });
  const setLogs = (l: LogRetention) => setForm({ ...form, logs: l });
  const saving = update.isPending || updateLogs.isPending;
  const saveError = update.error ?? updateLogs.error;

  const onSave = () => {
    const pDirty = !sameProgram(form.program, baseline!.program);
    const lDirty = !sameLogs(form.logs, baseline!.logs);
    let toasted = false;
    if (pDirty) {
      update.mutate(form.program, {
        onSuccess: () => {
          advance((base) => ({ ...base!, program: form.program }));
          if (!toasted) { toasted = true; toast.success("系统配置已保存"); }
        },
      });
    }
    if (lDirty) {
      updateLogs.mutate(form.logs, {
        onSuccess: () => {
          advance((base) => ({ ...base!, logs: form.logs }));
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
          <InfoTile label="运行时长" value={formatClock(now / 1000 - info.started_at)} valueClass="break-all text-foreground" />
        </div>
      )}

      <SectionTitle>监听与运行</SectionTitle>
      <FieldGrid>
        <Field label="监听地址" htmlFor="cfg-host">
          <TextInput id="cfg-host" value={form.program.host} onChange={(e) => set({ ...form.program, host: e.target.value })} />
        </Field>
        <Field label="监听端口" htmlFor="cfg-port"
          error={form.program.port !== 0 ? portError(form.program.port) : null}>
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
          error={saveError ? errMsg(saveError) : null}
          onSave={onSave}
          onReset={() => commit(initial)}
          saveDisabled={!portValid || !aliveValid || form.logs.days < 1 || form.logs.count < 1}
        />
      )}

      <SectionTitle>更新</SectionTitle>
      <UpdatePanel />
    </div>
  );
}
