import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Field, TextArea, TextInput } from "@/components/ui/form";
import { useConfirm } from "@/components/ui/dialog";
import { useToast } from "@/components/ui/toast";
import {
  useApplyClaudePreset,
  useClaudeCurrent,
  useConfig,
  useUpdateClaudeConfigs,
  useUpdateProgram,
} from "@/lib/use-config";

// 解析预设 JSON 文本:须为 str→str 对象。失败 → { ok:false, message }。
function parseEnvJson(text: string): { ok: true; value: Record<string, string> } | { ok: false; message: string } {
  try {
    const v: unknown = JSON.parse(text);
    if (typeof v !== "object" || v === null || Array.isArray(v)) {
      return { ok: false, message: "JSON 须为对象,如 {\"ANTHROPIC_BASE_URL\": \"http://...\"}" };
    }
    for (const [k, val] of Object.entries(v as Record<string, unknown>)) {
      if (typeof val !== "string") return { ok: false, message: `键「${k}」的值须为字符串` };
    }
    return { ok: true, value: v as Record<string, string> };
  } catch (e) {
    return { ok: false, message: `JSON 格式错误:${(e as Error).message}` };
  }
}

// ── 单张方案卡片 ─────────────────────────────────────────────
// mode="edit":已保存预设,name/preset 由父级传入,名字只读(创建后锁定);
// mode="new":新建卡,名字可输入、自动展开、不可折叠,保存成功后 onCreated。
// 折叠只隐藏身体(组件保持挂载),编辑不丢失;脏且折叠时头部显示「未保存」。
interface ClaudePresetCardProps {
  presets: Record<string, Record<string, string>>; // 全部预设(保存/删除 = 整组 PUT)
  name?: string;                                    // edit 模式:预设名(只读)
  preset?: Record<string, string>;                  // edit 模式:预设内容
  names: string[];                                  // 全部预设名(新建时查重)
  isCurrent: boolean;                               // 是否当前生效
  mode?: "edit" | "new";
  onCreated?: (name: string) => void;               // 新建保存成功(面板移除新建卡)
  onCancelNew?: () => void;                         // 新建卡「取消」
}

export function ClaudePresetCard({
  presets,
  name,
  preset,
  names,
  isCurrent,
  mode = "edit",
  onCreated,
  onCancelNew,
}: ClaudePresetCardProps) {
  const isNew = mode === "new";
  const update = useUpdateClaudeConfigs();   // 保存
  const del = useUpdateClaudeConfigs();      // 删除
  const apply = useApplyClaudePreset();
  const confirm = useConfirm();
  const toast = useToast();

  // 默认:生效中的卡展开,其余收起;新建卡必展开。
  const [expanded, setExpanded] = useState(isNew || isCurrent);
  const [nameInput, setNameInput] = useState("");
  const [json, setJson] = useState(isNew ? "{}" : JSON.stringify(preset, null, 2));
  const [baselineJson, setBaselineJson] = useState(json);

  const parsed = parseEnvJson(json);
  const jsonErr = parsed && !parsed.ok ? parsed.message : null;
  const nameOk = !isNew || nameInput.trim().length > 0;
  const collision = isNew && nameInput.trim() !== "" && names.includes(nameInput.trim());
  const dirty = isNew || json !== baselineJson;
  const editName = isNew ? nameInput.trim() : (name ?? "");

  const saveEnabled = dirty && nameOk && !collision && !jsonErr && !update.isPending;
  const applyEnabled = !isNew && !dirty && !isCurrent && !jsonErr;
  const deleteEnabled = !isNew && !dirty;
  const mutError = update.error ?? del.error ?? apply.error;

  const onSave = () => {
    if (!parsed?.ok || !editName) return;
    const next = { ...presets, [editName]: parsed.value };
    update.mutate(next, {
      onSuccess: () => {
        setBaselineJson(json);
        if (isNew) {
          toast.success(`已创建预设「${editName}」`);
          onCreated?.(editName);
        } else {
          toast.success("已保存");
        }
      },
    });
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
    const next = { ...presets };
    delete next[editName];
    del.mutate(next, { onSuccess: () => toast.success(`已删除预设「${editName}」`) });
  };

  const onApply = () => {
    if (isNew || !editName) return;
    apply.mutate(editName, { onSuccess: (r) => toast.success(`已应用预设「${r.applied}」`) });
  };

  return (
    <div className="rounded-lg border border-border p-3">
      {/* 头部:折叠后也常驻(应用/删除/生效标记都在) */}
      <div className="flex items-center gap-2">
        {!isNew && (
          <button
            type="button"
            className="w-4 text-xs text-muted-foreground hover:text-foreground"
            aria-label={expanded ? "收起" : "展开"}
            onClick={() => setExpanded(!expanded)}
          >
            {expanded ? "▾" : "▸"}
          </button>
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
        <Button type="button" size="sm" variant="ghost" onClick={onApply} disabled={!applyEnabled || apply.isPending}>
          {apply.isPending ? "应用中…" : "应用"}
        </Button>
        <Button
          type="button"
          size="sm"
          variant="ghost"
          className="text-destructive"
          onClick={onDelete}
          disabled={(!isNew && !deleteEnabled) || del.isPending}
        >
          {del.isPending ? "删除中…" : isNew ? "取消" : "删除"}
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
            {update.isPending ? "保存中…" : "保存"}
          </Button>
        </div>
        {mutError && <p className="mt-2 text-xs text-destructive">操作失败:{(mutError as Error).message}</p>}
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
  const toast = useToast();

  const serverPresets = data?.claude ?? {};
  const names = [...Object.keys(serverPresets)].sort();
  const current = currentData?.current ?? "";
  const currentMissing = current !== "" && current !== "(未知)" && !(current in serverPresets);

  // 新建卡:nonce>0 时渲染一张;保存成功(onCreated)或取消后清零,期间禁新增。
  const [newNonce, setNewNonce] = useState(0);

  // ── Claude settings 路径行(自通用页移入)──
  // 本地输入 + 最近保存值;外部刷新且未编辑(pathSaved === pathInput)时跟随。
  const [pathInput, setPathInput] = useState("");
  const [pathSaved, setPathSaved] = useState("");
  useEffect(() => {
    if (!data) return;
    if (pathSaved === pathInput) {
      setPathInput(data.program.claude_settings_path);
      setPathSaved(data.program.claude_settings_path);
    }
  }, [data, pathInput, pathSaved]);
  const pathDirty = pathInput !== pathSaved;
  const onSavePath = () => {
    updateProgram.mutate(
      { claude_settings_path: pathInput },
      {
        onSuccess: () => {
          setPathSaved(pathInput);
          toast.success("Claude settings 路径已保存");
        },
        onError: (e: unknown) => toast.error((e as Error).message),
      },
    );
  };

  if (isError) {
    return (
      <div className="flex items-center gap-2 text-sm text-destructive">
        加载失败:{(error as Error).message}
        <Button size="sm" variant="ghost" onClick={() => refetch()}>重试</Button>
      </div>
    );
  }
  if (isLoading) {
    return <div className="text-sm text-muted-foreground">加载中…</div>;
  }

  return (
    <div className="flex flex-col gap-3">
      <Field label="Claude settings 路径" hint="改完需重启生效(顶部会提示)" htmlFor="csp-path">
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
          presets={serverPresets}
          name={n}
          preset={serverPresets[n]}
          names={names}
          isCurrent={n === current}
        />
      ))}

      {newNonce > 0 && (
        <ClaudePresetCard
          key={`new-${newNonce}`}
          presets={serverPresets}
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
        className="justify-start self-start"
        disabled={newNonce > 0}
        onClick={() => setNewNonce((n) => n + 1)}
      >
        + 新增方案
      </Button>
    </div>
  );
}
