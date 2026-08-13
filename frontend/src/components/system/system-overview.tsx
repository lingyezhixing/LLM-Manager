// 系统页顶部「系统与更新」信息条:程序信息(启动时间、运行时长)与自更新
// (检测模式 版本|提交、当前版本(版本(提交短号))、最新(落后 N)、检查更新/更新)合并在第一行。
//
// 检测语义:程序启动时后端后台检测一次并缓存,前端只读缓存——刷新/进页不触发检测;
// 「检查更新」按钮(POST /check)是唯一手动重新检测入口;「更新」仅在所选模式目标
// 有更新时可点。git 不可用或非 git 仓库(后端 supported=false)→ 隐藏更新功能,
// 仅保留启动时间/运行时长。
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { InfoTile } from "@/components/ui/info-tile";
import type { UpdateStatus, UpdateTarget } from "@/lib/api";
import { formatClock } from "@/lib/format";
import { useConfirm } from "@/lib/hooks/use-confirm";
import { useToast } from "@/lib/hooks/use-toast";
import { useNowTick } from "@/lib/hooks/use-now-tick";
import { useSystemInfo, useUpdateApp, useUpdateCheck, useUpdateStatus } from "@/lib/hooks/use-config";

const MODES: readonly { key: UpdateTarget; label: string }[] = [
  { key: "tag", label: "版本" },
  { key: "commit", label: "提交" },
];

const isAvailable = (st: UpdateStatus, mode: UpdateTarget) =>
  mode === "tag" ? st.tag_available : st.commit_available;
const otherAvailable = (st: UpdateStatus, mode: UpdateTarget) =>
  mode === "tag" ? st.commit_available : st.tag_available;
const latestVal = (st: UpdateStatus, mode: UpdateTarget) =>
  mode === "tag" ? (st.tag ?? "—") : (st.commit_sha ?? "—");
const behindOf = (st: UpdateStatus, mode: UpdateTarget) =>
  mode === "tag" ? st.tag_behind : st.commit_behind;

export function SystemOverview() {
  const { data: info } = useSystemInfo();
  const now = useNowTick(1000);
  const [mode, setMode] = useState<UpdateTarget>("tag");
  const { data: st, isFetching, isError } = useUpdateStatus();
  const check = useUpdateCheck();
  const app = useUpdateApp();
  const confirm = useConfirm();
  const toast = useToast();

  // git 不可用 / 非 git 仓库 → 隐藏整个更新功能(仅剩启动时间/运行时长)
  const unsupported = !!st && !st.supported;

  const onCheck = () => check.mutate();

  const onUpdate = async () => {
    const label = latestVal(st!, mode);
    const ok = await confirm({
      title: `${mode === "tag" ? "最新标签" : "最新提交"} ${label} — 更新并重启?`,
      description: "将拉取最新代码并重启程序,期间正在服务的模型会中断。仅向前更新,不支持回退;与本地未提交改动冲突时会被拒绝。",
      confirmText: "更新并重启",
      cancelText: "取消",
    });
    if (!ok) return;
    app.triggerUpdate(mode);
    toast.success("更新已开始,页面将在恢复后自动刷新");
  };

  const canUpdate = !!st && !st.conflicted && isAvailable(st, mode);
  const behind = st ? behindOf(st, mode) : 0;
  const latest = st ? latestVal(st, mode) : "—";
  // 当前版本 = 版本标签(提交短号),如 v3.0.0a2(f7dbf33);有其一则只显示其一
  const ver = st?.current_version ?? "";
  const sha = st?.current_sha ?? "";
  const currentVersion = ver && sha ? `${ver}(${sha})` : ver || sha || "—";
  // 加载中(缓存未就绪)不显示误导性禁用原因
  const updateTitle = st === undefined
    ? undefined
    : canUpdate
      ? undefined
      : st.conflicted
        ? "本地与远端历史分叉,无法更新"
        : mode === "tag" && !st.tag
          ? "远端无标签版本,可切换检测模式为「提交」"
          : "当前目标已是最新,无更新";

  let note: string | null = null;
  let noteClass = "text-muted-foreground";
  if (st?.checking || (isFetching && !st)) {
    note = "正在检查更新…(程序启动时自动检测)";
  } else if (check.isPending) {
    note = "正在检查更新…";
  } else if (st) {
    if (!st.ok) {
      note = `检查失败:${st.error ?? "未知错误"}`;
      noteClass = "text-destructive";
    } else if (st.conflicted) {
      note = "本地与远端历史分叉,无法更新,需手动处理";
      noteClass = "text-warning";
    } else if (isAvailable(st, mode)) {
      note = `发现新版本 — ${latest}`;
      noteClass = "text-success";
    } else if (mode === "tag" && !st.tag) {
      note = "远端无标签版本 — 可切换检测模式为「提交」";
    } else if (otherAvailable(st, mode)) {
      note = `最新${mode === "tag" ? "标签" : "提交"}已是最新 — 可切换检测模式为「${mode === "tag" ? "提交" : "版本"}」`;
    } else {
      note = "已是最新版本";
      noteClass = "text-success";
    }
  } else if (isError) {
    note = "获取更新信息失败,点击「检查更新」重试";
    noteClass = "text-destructive";
  } else {
    note = "点击「检查更新」手动检测最新版本";
  }

  const checking = check.isPending || app.pending || st?.checking;

  return (
    <div className="mb-4">
      {!unsupported && (
        <div className="mb-2 flex flex-wrap items-center gap-3">
          <span className="text-sm font-medium text-foreground">系统与更新</span>
          <div className="flex items-center gap-2">
            <span className="text-xs text-muted-foreground">检测模式</span>
            <div className="flex items-center gap-1">
              {MODES.map((m) => (
                <button
                  key={m.key}
                  type="button"
                  onClick={() => setMode(m.key)}
                  className={`rounded-full border border-border px-2.5 py-0.5 text-[11px] ${
                    mode === m.key ? "bg-primary-accent/12 font-medium text-primary-accent" : "text-muted-foreground hover:text-foreground"
                  }`}
                >
                  {m.label}
                </button>
              ))}
            </div>
          </div>
          <div className="ml-auto flex gap-2">
            <Button type="button" variant="outline" size="sm" onClick={onCheck} disabled={checking}>
              {check.isPending ? "检查中…" : "检查更新"}
            </Button>
            <Button
              type="button"
              variant="default"
              size="sm"
              onClick={onUpdate}
              disabled={!canUpdate || app.pending}
              title={updateTitle}
            >
              {app.pending ? "更新中…" : "更新"}
            </Button>
          </div>
        </div>
      )}

      <div className={`grid grid-cols-2 gap-2 ${unsupported ? "" : "sm:grid-cols-2 lg:grid-cols-4"}`}>
        {!unsupported && (
          <>
            <InfoTile label="当前版本" value={currentVersion} valueClass="text-foreground" />
            <InfoTile
              label={mode === "tag" ? "最新版本" : "最新提交"}
              value={`${latest}${behind > 0 ? ` (落后 ${behind})` : ""}`}
              valueClass="text-foreground"
            />
          </>
        )}
        <InfoTile
          label="启动时间"
          value={info ? new Date(info.started_at * 1000).toLocaleString() : "—"}
          valueClass="text-foreground"
        />
        <InfoTile label="运行时长" value={info ? formatClock(now / 1000 - info.started_at) : "—"} valueClass="text-foreground" />
      </div>

      {!unsupported && (
        <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1">
          {note && <p className={`text-sm ${noteClass}`}>{note}</p>}
          {st?.dirty && <p className="text-xs text-warning">工作树有未提交改动,仅与更新冲突时被拒</p>}
        </div>
      )}

      {!unsupported && app.error && (
        <div className="mt-2 flex flex-wrap items-center gap-3 rounded-md border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive">
          <span>{app.error}</span>
          <Button size="sm" variant="ghost" onClick={() => window.location.reload()}>刷新页面</Button>
        </div>
      )}
      {!unsupported && app.updating && (
        <div className="mt-2 rounded-md border border-border bg-muted p-3 text-sm text-muted-foreground">
          正在更新并重启,页面将在恢复后自动刷新…
        </div>
      )}
    </div>
  );
}
