// 系统页顶部「系统与更新」信息条:程序信息(当前版本/提交、启动时间、运行时长)
// 与自更新检测(检测模式 版本|提交、最新版本/提交(落后 N)、检查更新/更新)合并在第一行。
// 程序启动时自动检测一次(见 App.tsx StartupUpdateCheck),此后仅手动点「检查更新」才联网;
// 「更新」按钮仅在所选模式目标有更新时可用。仅向前更新(ff-only),无回退/版本选择/提交浏览。
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { InfoTile } from "@/components/ui/info-tile";
import type { UpdateStatus, UpdateTarget } from "@/lib/api";
import { errMsg, formatClock } from "@/lib/format";
import { useConfirm } from "@/lib/hooks/use-confirm";
import { useToast } from "@/lib/hooks/use-toast";
import { useNowTick } from "@/lib/hooks/use-now-tick";
import { useSystemInfo, useUpdateApp, useUpdateStatus } from "@/lib/hooks/use-config";

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
  const { data: st, error, isError, isFetching, refetch } = useUpdateStatus();
  const app = useUpdateApp();
  const confirm = useConfirm();
  const toast = useToast();

  const onCheck = () => {
    refetch().catch(() => {});
  };

  const onUpdate = async () => {
    const label = latestVal(st!, mode);
    const ok = await confirm({
      title: `${mode === "tag" ? "最新标签" : "最新提交"} ${label} — 更新并重启?`,
      description: "将拉取最新代码并重启程序,期间正在服务的模型会中断。仅向前更新,不支持回退;与本地未提交改动冲突时会被拒绝。",
      confirmText: "更新并重启",
      cancelText: "取消",
      danger: true,
    });
    if (!ok) return;
    app.triggerUpdate(mode);
    toast.success("更新已开始,页面将在恢复后自动刷新");
  };

  const canUpdate = !!st && !st.conflicted && isAvailable(st, mode);
  const behind = st ? behindOf(st, mode) : 0;
  const latest = st ? latestVal(st, mode) : "—";

  let note: string | null = null;
  let noteClass = "text-muted-foreground";
  if (isFetching && !st) {
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
    note = `检查失败:${errMsg(error)}`;
    noteClass = "text-destructive";
  } else {
    note = "启动时已自动检测一次,之后仅手动点「检查更新」";
  }

  return (
    <div className="mb-4">
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
          <Button type="button" variant="outline" size="sm" onClick={onCheck} disabled={isFetching || app.pending}>
            {isFetching ? "检查中…" : "检查更新"}
          </Button>
          <Button
            type="button"
            variant="destructive"
            size="sm"
            onClick={onUpdate}
            disabled={!canUpdate || app.pending}
            title={canUpdate ? undefined : "当前目标无更新"}
          >
            {app.pending ? "更新中…" : "更新"}
          </Button>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5">
        <InfoTile label="当前版本" value={st?.current_version ?? "—"} valueClass="text-foreground" />
        <InfoTile label="当前提交" value={st?.current_sha ?? "—"} valueClass="text-foreground" />
        <InfoTile
          label={mode === "tag" ? "最新版本" : "最新提交"}
          value={`${latest}${behind > 0 ? `(落后 ${behind})` : ""}`}
          valueClass="text-foreground"
        />
        <InfoTile
          label="启动时间"
          value={info ? new Date(info.started_at * 1000).toLocaleString() : "—"}
          valueClass="text-foreground"
        />
        <InfoTile label="运行时长" value={info ? formatClock(now / 1000 - info.started_at) : "—"} valueClass="text-foreground" />
      </div>

      <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1">
        {note && <p className={`text-sm ${noteClass}`}>{note}</p>}
        {st?.dirty && <p className="text-xs text-warning">工作树有未提交改动,仅与更新冲突时被拒</p>}
      </div>

      {app.error && (
        <div className="mt-2 flex flex-wrap items-center gap-3 rounded-md border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive">
          <span>{app.error}</span>
          <Button size="sm" variant="ghost" onClick={() => window.location.reload()}>刷新页面</Button>
        </div>
      )}
      {app.updating && (
        <div className="mt-2 rounded-md border border-border bg-muted p-3 text-sm text-muted-foreground">
          正在更新并重启,页面将在恢复后自动刷新…
        </div>
      )}
    </div>
  );
}
