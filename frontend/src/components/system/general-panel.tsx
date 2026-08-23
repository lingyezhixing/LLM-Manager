import { type ReactNode, useMemo, useState } from "react";
import { ConfigSaveBar } from "@/components/config-save-bar";
import { Loading } from "@/components/ui/card";
import { ErrorState } from "@/components/ui/error-state";
import { Field, NumberInput, Select, TextInput } from "@/components/ui/form";
import { errMsg, numFromStr as num, portError } from "@/lib/format";
import { useToast } from "@/lib/hooks/use-toast";
import { useConfirm } from "@/lib/hooks/use-confirm";
import { LogRetentionEditor } from "@/components/system/log-retention-editor";
import { SystemOverview } from "@/components/system/system-overview";
import { type ConfigWriteResult, type LogRetention, type ProgramConfig, updateProgram } from "@/lib/api";
import {
  useConfig,
  useRestartApp,
  useUpdateLogRetention,
  useUpdateProgram,
} from "@/lib/hooks/use-config";
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
  const [confirming, setConfirming] = useState(false);
  const { data, isLoading, isError, error, refetch } = useConfig();
  const update = useUpdateProgram();
  const updateLogs = useUpdateLogRetention();
  const toast = useToast();
  const confirm = useConfirm();
  const { triggerRestart } = useRestartApp();
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
  const saving = update.isPending || updateLogs.isPending || confirming;
  const saveError = update.error ?? updateLogs.error;

  const onSave = async () => {
    if (saving) return;   // 确认窗期间禁止连点(防重复预检/确认排队)
    setConfirming(true);
    try {
      const pDirty = !sameProgram(form.program, baseline!.program);
      const lDirty = !sameLogs(form.logs, baseline!.logs);
      let toasted = false;
      const gotoSavedToast = () => {
        if (!toasted) {
          toasted = true;
          toast.success("系统配置已保存");
        }
      };
      if (pDirty) {
        // 先预检(不落库):是否涉及重启字段 → 有则必须二选一(确认后落库+重启;取消=零副作用)。
        let preview: ConfigWriteResult;
        try {
          preview = await updateProgram(form.program, true);
        } catch (e) {
          toast.error(errMsg(e));
          return;
        }
        if (preview.restart_fields.length > 0) {
          const ok = await confirm({
            title: "保存将要求程序重启",
            description:
              `以下变更生效需重启:${preview.restart_fields.join("、")}` +
              (preview.serving.length > 0
                ? `。当前正在服务的模型(${preview.serving.join("、")})会被重启中断。`
                : "。") +
              "重启后继续服务,配置即刻生效。",
            confirmText: "保存并重启",
            cancelText: "取消(不保存)",
          });
          if (!ok) return;
        }
        update.mutate(form.program, {
          onSuccess: () => {
            advance((base) => ({ ...base!, program: form.program }));
            gotoSavedToast();
            if (preview.restart_fields.length > 0) triggerRestart();
          },
        });
      }
      if (lDirty) {
        updateLogs.mutate(form.logs, {
          onSuccess: () => {
            advance((base) => ({ ...base!, logs: form.logs }));
            gotoSavedToast();
          },
        });
      }
    } finally {
      setConfirming(false);
    }
  };

  return (
    <div>
      <SystemOverview />

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
    </div>
  );
}
