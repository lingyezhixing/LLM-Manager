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
    return { ok: false, message: (e as Error).message };
  }
}

// 编辑态:key = 服务端预设名(null = 新建);baseline* = 最近一次保存/加载时的值(dirty 基准)。
interface Editing {
  key: string | null;
  name: string;
  json: string;
  baselineName: string;
  baselineJson: string;
  isNew: boolean;
}

// 系统页「Claude」区:预设管理(名称 + 环境变量 JSON)。编辑先在本地,保存 = 整组 PUT;
// 应用 = 把该预设写入 Claude settings.json(与托盘同逻辑)。删除/应用在有未保存修改时禁用。
export function ClaudePanel() {
  const { data, isLoading, isError, error, refetch } = useConfig();
  const update = useUpdateClaudeConfigs();
  const del = useUpdateClaudeConfigs();
  const apply = useApplyClaudePreset();
  const { data: currentData } = useClaudeCurrent();
  const confirm = useConfirm();
  const toast = useToast();

  const serverPresets = data?.claude ?? {};
  const names = [...Object.keys(serverPresets)].sort();
  const [editing, setEditing] = useState<Editing | null>(null);

  // 数据就绪且未在编辑 → 默认选第一个(仅初始载入;删除后的重选在 onDelete 内显式处理)。
  useEffect(() => {
    if (!data || editing !== null) return;
    const first = Object.keys(data.claude).sort()[0];
    if (first) {
      const json = JSON.stringify(data.claude[first], null, 2);
      setEditing({ key: first, name: first, json, baselineName: first, baselineJson: json, isNew: false });
    }
  }, [data, editing]);

  const parsed = editing ? parseEnvJson(editing.json) : null;
  const jsonErr = parsed && !parsed.ok ? parsed.message : null;
  const nameOk = !!editing?.name.trim();
  const collision = editing !== null && editing.key !== editing.name && editing.name in serverPresets;
  const dirty = editing !== null && (editing.isNew || editing.name !== editing.baselineName || editing.json !== editing.baselineJson);
  const current = currentData?.current ?? "";

  const saveEnabled = !!editing && dirty && !jsonErr && nameOk && !collision;
  const applyEnabled = !!editing && !editing.isNew && !dirty && !jsonErr && editing.key !== null && editing.key !== current;
  const deleteEnabled = !!editing && !editing.isNew && !dirty;

  const startCreate = () => {
    const used = new Set(names);
    let name = "新预设";
    for (let i = 2; used.has(name); i++) name = `新预设 ${i}`;
    setEditing({ key: null, name, json: "{}", baselineName: name, baselineJson: "{}", isNew: true });
  };

  const onSave = () => {
    if (!editing || !parsed?.ok) return;
    const next = { ...serverPresets, [editing.name]: parsed.value };
    if (editing.key !== null && editing.key !== editing.name) delete next[editing.key];  // 改名:移除旧键
    update.mutate(next, {
      onSuccess: () => {
        setEditing({ ...editing, key: editing.name, baselineName: editing.name, baselineJson: editing.json, isNew: false });
        toast.success(editing.isNew ? `已创建预设「${editing.name}」` : "已保存");
      },
    });
  };

  const onDelete = async () => {
    if (!editing || editing.key === null) return;
    const name = editing.key;
    const ok = await confirm({
      title: `删除预设 ${name}?`,
      description: "此操作不可撤销。",
      confirmText: "删除",
      cancelText: "取消",
      danger: true,
    });
    if (!ok) return;
    const next = { ...serverPresets };
    delete next[name];
    del.mutate(next, {
      onSuccess: () => {
        // 显式重选(不依赖异步 refetch):剩余第一个,或空态。
        const remaining = Object.keys(next).sort();
        setEditing(
          remaining.length === 0
            ? null
            : {
                key: remaining[0],
                name: remaining[0],
                json: JSON.stringify(next[remaining[0]], null, 2),
                baselineName: remaining[0],
                baselineJson: JSON.stringify(next[remaining[0]], null, 2),
                isNew: false,
              },
        );
        toast.success(`已删除预设「${name}」`);
      },
    });
  };

  const onApply = () => {
    if (!editing || editing.key === null) return;
    apply.mutate(editing.key, {
      onSuccess: (r) => toast.success(`已应用预设「${r.applied}」`),
    });
  };

  const mutError = update.error ?? del.error ?? apply.error;

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
    <div className="grid gap-6 lg:grid-cols-[minmax(0,280px)_minmax(0,1fr)]">
      {/* 左栏:预设列表 + 新增 */}
      <div className="flex flex-col gap-1">
        <div className="flex flex-col gap-0.5" role="listbox" aria-label="Claude 预设列表">
          {names.length === 0 && (
            <span className="px-3 py-2 text-sm text-muted-foreground">暂无预设</span>
          )}
          {names.map((n) => {
            const selected = editing !== null && editing.key === n;
            return (
              <button
                key={n}
                type="button"
                role="option"
                aria-selected={selected}
                onClick={() => {
                  const json = JSON.stringify(serverPresets[n], null, 2);
                  setEditing({ key: n, name: n, json, baselineName: n, baselineJson: json, isNew: false });
                }}
                className={
                  "rounded-md px-3 py-2 text-left text-sm transition-colors " +
                  (selected
                    ? "bg-muted font-medium text-foreground"
                    : "text-muted-foreground hover:bg-muted hover:text-foreground")
                }
              >
                {n}
              </button>
            );
          })}
        </div>
        <div className="flex flex-col gap-1 border-t border-border pt-2">
          <Button type="button" size="sm" variant="ghost" onClick={startCreate} className="w-full justify-start">
            + 新增
          </Button>
        </div>
      </div>

      {/* 右栏:名称 + JSON 输入框 + 删除/保存/应用 */}
      <div>
        {editing === null ? (
          <div className="rounded-lg border border-dashed border-border p-16 text-center text-sm text-muted-foreground">
            选择或新增一个预设
          </div>
        ) : (
          <>
            <div className="grid grid-cols-1 gap-x-6 sm:grid-cols-2">
              <Field label="预设名" hint={editing.key === null ? "保存后生效" : undefined} htmlFor="cp-name">
                <TextInput id="cp-name" value={editing.name} onChange={(e) => setEditing({ ...editing, name: e.target.value })} />
              </Field>
              <Field label="当前生效" hint="点击「应用」后更新">
                <span className="flex h-9 items-center text-sm text-muted-foreground">{current || "—"}</span>
              </Field>
            </div>
            <Field
              label="环境变量 JSON"
              hint="写入 Claude settings.json 的 env;键值须为字符串"
              error={jsonErr ?? (collision ? "预设名与现有预设重复" : null)}
            >
              <TextArea
                id="cp-json"
                value={editing.json}
                onChange={(e) => setEditing({ ...editing, json: e.target.value })}
                className="min-h-40"
              />
            </Field>
            <div className="mt-2 flex items-center gap-2">
              <Button type="button" size="sm" variant="ghost" className="text-destructive" onClick={onDelete} disabled={!deleteEnabled || del.isPending}>
                删除
              </Button>
              <div className="flex-1" />
              <Button type="button" size="sm" variant="ghost" onClick={onApply} disabled={!applyEnabled || apply.isPending}>
                {apply.isPending ? "应用中…" : "应用"}
              </Button>
              <Button type="button" size="sm" onClick={onSave} disabled={!saveEnabled || update.isPending}>
                {update.isPending ? "保存中…" : "保存"}
              </Button>
            </div>
            {mutError && <p className="mt-2 text-xs text-destructive">操作失败:{(mutError as Error).message}</p>}
          </>
        )}
      </div>
    </div>
  );
}
