import { useEffect, useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Loading } from "@/components/ui/card";
import { ErrorState } from "@/components/ui/error-state";
import { Field, TextArea, TextInput } from "@/components/ui/form";
import { useConfirm } from "@/lib/hooks/use-confirm";
import { useToast } from "@/lib/hooks/use-toast";
import { errMsg } from "@/lib/format";
import { type ConfigResponse, type ConfigWriteResult, updateProgram as updateProgramApi } from "@/lib/api";
import { useConfig, useRestartApp, useUpdateProgram } from "@/lib/hooks/use-config";
import { useApplyClaudePreset, useClaudeCurrent, useUpdateClaudeConfigs } from "@/lib/hooks/use-tools";
import { useSyncedForm } from "@/lib/hooks/use-synced-form";
import { qk } from "@/lib/api/keys";
import { parseEnvJson, jsonEq } from "@/lib/tools/claude-json";

// ── 单张方案卡片 ─────────────────────────────────────────────
// mode="edit":已保存预设,name/preset 由父级传入,名字只读(创建后锁定);
// mode="new":新建卡,名字可输入、自动展开、不可折叠,保存成功后 onCreated。
// 折叠只隐藏身体(组件保持挂载),编辑不丢失;脏且折叠时头部显示「未保存」。
interface ClaudePresetCardProps {
  name?: string;                                    // edit 模式:预设名(只读)
  preset?: Record<string, string>;                  // edit 模式:预设内容
  names: string[];                                  // 全部预设名(新建时查重)
  isCurrent: boolean;                               // 是否当前生效
  mode?: "edit" | "new";
  onCreated?: (name: string) => void;               // 新建保存成功(面板移除新建卡)
  onCancelNew?: () => void;                         // 新建卡「取消」
}

function ClaudePresetCard({
  name,
  preset,
  names,
  isCurrent,
  mode = "edit",
  onCreated,
  onCancelNew,
}: ClaudePresetCardProps) {
  const isNew = mode === "new";
  const update = useUpdateClaudeConfigs();   // 保存 / 删除均整组 PUT(同一 mutation)
  const apply = useApplyClaudePreset();
  const confirm = useConfirm();
  const toast = useToast();
  const queryClient = useQueryClient();

  // 保存/删除时从查询缓存读最新 presets 构造 payload,而非用父级传入的 presets 快照——
  // 两卡连续保存时,后保存者的快照可能不含先保存者的改动 → 整组 PUT 覆盖丢失(lost update)。
  const latestPresets = (): Record<string, Record<string, string>> =>
    (queryClient.getQueryData<ConfigResponse>(qk.config)?.claude) ?? {};

  // 默认:生效中的卡展开,其余收起;新建卡必展开。
  const [expanded, setExpanded] = useState(isNew || isCurrent);
  // isCurrent 异步到达(useClaudeCurrent 首载未回)→ 补展开(挂载初值固化后不会重算)。
  // 用户手动收起后依赖不变,不会强制展开。
  useEffect(() => {
    if (!isNew && isCurrent) setExpanded(true);
  }, [isNew, isCurrent]);
  const [nameInput, setNameInput] = useState("");
  // useSyncedForm:json/baseline 同步契约(外部刷新且未编辑时跟随,保存成功 commit 推进)。
  // 新建卡 serverValue=null(不进外部跟随)+ alwaysDirty(恒脏);编辑卡以 JSON 文本为 form。
  const { form: json, setForm: setJson, dirty, commit } = useSyncedForm<string>(
    isNew ? null : JSON.stringify(preset, null, 2),
    JSON.stringify(preset ?? {}, null, 2),
    jsonEq,
    { alwaysDirty: isNew },
  );

  const parsed = parseEnvJson(json);
  const jsonErr = parsed && !parsed.ok ? parsed.message : null;
  const nameOk = !isNew || nameInput.trim().length > 0;
  const collision = isNew && nameInput.trim() !== "" && names.includes(nameInput.trim());
  const editName = isNew ? nameInput.trim() : (name ?? "");

  const saveEnabled = dirty && nameOk && !collision && !jsonErr && !update.isPending;
  const applyEnabled = !isNew && !dirty && !isCurrent && !jsonErr;
  const deleteEnabled = !isNew && !dirty;
  const mutError = update.error ?? apply.error;

  const onSave = () => {
    if (!parsed?.ok || !editName) return;
    const next = { ...latestPresets(), [editName]: parsed.value };
    // 当前生效预设 → 「保存并生效」:整组 PUT 带 apply,后端保存后同步写 settings.json。
    update.mutate(
      { configs: next, apply: isCurrent ? editName : undefined },
      {
        onSuccess: () => {
          commit(json);
          if (isNew) {
            toast.success(`已创建预设「${editName}」`);
            onCreated?.(editName);
          } else {
            toast.success(isCurrent ? `已保存并生效「${editName}」` : "已保存");
          }
        },
      },
    );
  };

  const onDelete = async () => {
    if (isNew) { onCancelNew?.(); return; }
    const ok = await confirm({
      title: `删除预设 ${editName}?`,
      description: "此操作不可撤销。",
      confirmText: "删除",
      cancelText: "取消",
      danger: true,
    });
    if (!ok) return;
    const next = { ...latestPresets() };
    delete next[editName];
    update.mutate({ configs: next }, { onSuccess: () => toast.success(`已删除预设「${editName}」`) });
  };

  const onApply = () => {
    if (isNew || !editName) return;
    apply.mutate(editName, { onSuccess: (r) => toast.success(`已应用预设「${r.applied}」`) });
  };

  return (
    <div className="rounded-md border border-border p-3">
      {/* 头部:折叠后也常驻(应用/删除/生效标记都在)。整条可点切换展开(新建卡除外,内部按钮 stopPropagation)。
          role=button + tabIndex + Enter/Space:键盘可激活(内嵌两个 button,不能用 button 元素实现)。 */}
      <div
        role="button"
        tabIndex={isNew ? -1 : 0}
        aria-expanded={isNew ? undefined : expanded}
        className={`flex select-none items-center gap-2 rounded focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring ${isNew ? "" : "cursor-pointer"}`}
        onClick={() => { if (!isNew) setExpanded(!expanded); }}
        onKeyDown={(e) => {
          if (isNew) return;
          if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setExpanded(!expanded); }
        }}
      >
        {!isNew && (
          <span className="text-muted-foreground" aria-hidden>
            {expanded ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />}
          </span>
        )}
        {isNew ? (
          <TextInput
            value={nameInput}
            onChange={(e) => setNameInput(e.target.value)}
            placeholder="方案名"
            className="w-44"
          />
        ) : (
          <span className="text-sm font-medium text-foreground">{name}</span>
        )}
        {isCurrent
          ? <span className="text-xs font-medium text-success">● 生效中</span>
          : <span className="text-xs text-muted-foreground">○ 未生效</span>}
        {!expanded && dirty && <span className="text-xs text-warning">未保存</span>}
        <div className="flex-1" />
        <Button type="button" size="sm" variant="ghost" onClick={(e) => { e.stopPropagation(); onApply(); }} disabled={!applyEnabled || apply.isPending}>
          {apply.isPending ? "应用中…" : "应用"}
        </Button>
        <Button
          type="button"
          size="sm"
          variant="ghost"
          className="text-destructive"
          onClick={(e) => { e.stopPropagation(); onDelete(); }}
          disabled={(!isNew && !deleteEnabled) || update.isPending}
        >
          {update.isPending ? "删除中…" : isNew ? "取消" : "删除"}
        </Button>
      </div>

      {/* 身体:仅展开时显示(折叠只隐藏,不卸载,编辑不丢失) */}
      <div className={expanded ? "mt-3" : "hidden"}>
        <Field
          label="环境变量 JSON"
          hint="写入 Claude settings.json 的 env;键值须为字符串"
          error={jsonErr ?? (collision ? "方案名与现有预设重复" : null)}
        >
          <TextArea value={json} onChange={(e) => setJson(e.target.value)} className="min-h-80" />
        </Field>
        <div className="mt-1 flex items-center justify-end gap-2">
          <Button type="button" size="sm" onClick={onSave} disabled={!saveEnabled}>
            {update.isPending ? "保存中…" : isCurrent ? "保存并生效" : "保存"}
          </Button>
        </div>
        {mutError && <p className="mt-2 text-xs text-destructive">操作失败:{errMsg(mutError)}</p>}
      </div>
    </div>
  );
}

// 系统页「Claude」区:顶部 settings 路径行 + 方案卡片列表 + 底部「+ 新增方案」。
// 每张卡独立保存/应用/删除;名字创建后锁定。current 不匹配任何预设时顶部警示。
export function ClaudePanel() {
  const { data, isLoading, isError, error, refetch } = useConfig();
  const { data: currentData } = useClaudeCurrent();
  const updateProgram = useUpdateProgram();
  const { triggerRestart } = useRestartApp();
  const confirm = useConfirm();
  const toast = useToast();
  const [confirming, setConfirming] = useState(false);
  // ── Claude settings 路径行 ──
  // useSyncedForm:外部刷新且未编辑(pathInput == baseline)时跟随,保存成功 commit 推进。
  const serverPath = data?.program.claude_settings_path ?? "";
  const { form: pathInput, setForm: setPathInput, dirty: pathDirty, commit: commitPath } =
    useSyncedForm<string>(serverPath, "", (a, b) => a === b);

  const serverPresets = data?.claude ?? {};
  const names = [...Object.keys(serverPresets)].sort();
  const current = currentData?.current ?? "";
  const currentMissing = current !== "" && current !== "(未知)" && !(current in serverPresets);

  // 新建卡:nonce>0 时渲染一张;保存成功(onCreated)或取消后清零,期间禁新增。
  const [newNonce, setNewNonce] = useState(0);

  const onSavePath = async () => {
    if (updateProgram.isPending || confirming) return;  // 确认窗期间防连点
    setConfirming(true);
    try {
      // claude_settings_path 是重启字段:先预检(不落库)→ 需重启则二选一,再落库+重启。
      let preview: ConfigWriteResult;
      try {
        preview = await updateProgramApi({ claude_settings_path: pathInput }, true);
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
              : "。"),
          confirmText: "保存并重启",
          cancelText: "取消(不保存)",
        });
        if (!ok) return;
      }
      updateProgram.mutate(
        { claude_settings_path: pathInput },
        {
          onSuccess: () => {
            commitPath(pathInput);
            toast.success("Claude settings 路径已保存");
            if (preview.restart_fields.length > 0) triggerRestart();
          },
          onError: (e: unknown) => toast.error(errMsg(e)),
        },
      );
    } finally {
      setConfirming(false);
    }
  };

  if (isError) {
    return <ErrorState message={errMsg(error)} onRetry={() => refetch()} />;
  }
  if (isLoading) {
    return <Loading />;
  }

  return (
    <div className="flex flex-col gap-3">
      <Field label="Claude settings 路径" hint="保存时若涉及需重启的变更,会先弹确认(保存并重启/取消不保存)" htmlFor="csp-path">
        <div className="flex items-center gap-2">
          <TextInput id="csp-path" value={pathInput} onChange={(e) => setPathInput(e.target.value)} className="flex-1" />
          <Button type="button" size="sm" onClick={onSavePath} disabled={!pathDirty || updateProgram.isPending}>
            {updateProgram.isPending ? "保存中…" : "保存"}
          </Button>
        </div>
      </Field>

      {currentMissing && (
        <div className="rounded-md border border-warning/40 bg-warning/10 px-3 py-2 text-sm text-foreground">
          当前生效的 settings.json 与任何预设都不匹配
        </div>
      )}

      {names.map((n) => (
        <ClaudePresetCard
          key={n}
          name={n}
          preset={serverPresets[n]}
          names={names}
          isCurrent={n === current}
        />
      ))}

      {newNonce > 0 && (
        <ClaudePresetCard
          key={`new-${newNonce}`}
          mode="new"
          names={names}
          isCurrent={false}
          onCreated={() => setNewNonce(0)}
          onCancelNew={() => setNewNonce(0)}
        />
      )}

      <Button
        type="button"
        variant="ghost"
        size="sm"
        className="justify-start self-start"
        disabled={newNonce > 0}
        onClick={() => setNewNonce((n) => n + 1)}
      >
        + 新增方案
      </Button>
    </div>
  );
}
