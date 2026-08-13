// 系统配置页「更新」区:git 标签版本身份 + 严格 ff-only(仅向前,无回退 / 无版本选择 / 无提交浏览)。
// 进入不自动检测——仅点「检查更新」才联网(fetch 对比,不动工作树);检测模式可切换
// 「版本」(最新标签,稳定发布)或「提交」(最新提交,前沿),两者共享同一次检查结果。
// 本地未提交改动不预拒——仅与更新内容冲突时后端拒绝。
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { InfoTile } from "@/components/ui/info-tile";
import type { UpdateStatus, UpdateTarget } from "@/lib/api";
import { errMsg } from "@/lib/format";
import { useConfirm } from "@/lib/hooks/use-confirm";
import { useToast } from "@/lib/hooks/use-toast";
import { useUpdateApp, useUpdateStatus } from "@/lib/hooks/use-config";

const MODES: readonly { key: UpdateTarget; label: string }[] = [
  { key: "tag", label: "版本" },
  { key: "commit", label: "提交" },
];

const isAvailable = (st: UpdateStatus, mode: UpdateTarget) =>
  mode === "tag" ? st.tag_available : st.commit_available;

function StateNote({ st, mode }: { st: UpdateStatus; mode: UpdateTarget }) {
  if (st.conflicted) {
    return <p className="text-sm text-warning">本地与远端历史分叉,无法更新,需手动处理</p>;
  }
  if (isAvailable(st, mode)) {
    return <p className="text-sm text-success">发现新版本,可更新</p>;
  }
  const other = mode === "tag" ? "提交" : "版本";
  if (mode === "tag" && !st.tag) {
    return <p className="text-sm text-muted-foreground">远端无标签版本 — 可切换目标为「{other}」查看最新提交</p>;
  }
  if (mode === "tag" ? st.commit_available : st.tag_available) {
    return <p className="text-sm text-muted-foreground">最新{mode === "tag" ? "标签" : "提交"}已是最新 — 可切换目标为「{other}」查看</p>;
  }
  return <p className="text-sm text-success">已是最新版本</p>;
}

export function UpdatePanel() {
  const [mode, setMode] = useState<UpdateTarget>("tag");
  const { data: st, error, isError, isFetching, refetch } = useUpdateStatus();
  const app = useUpdateApp();
  const confirm = useConfirm();
  const toast = useToast();
  const checked = st !== undefined;

  const onCheck = () => {
    refetch().catch(() => {});
  };

  const onApply = async () => {
    const label = mode === "tag"
      ? (st?.tag ? `最新标签 ${st.tag}` : "最新标签")
      : `最新提交 ${st?.commit_sha ?? ""}`;
    const ok = await confirm({
      title: `${label} 更新并重启?`,
      description: "将拉取最新代码并重启程序,期间正在服务的模型会中断。仅向前更新,不支持回退;与本地未提交改动冲突时会被拒绝。",
      confirmText: "更新并重启",
      cancelText: "取消",
      danger: true,
    });
    if (!ok) return;
    app.triggerUpdate(mode);
    toast.success("更新已开始,页面将在恢复后自动刷新");
  };

  const available = !!st && !st.conflicted && isAvailable(st, mode);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3">
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
            onClick={onApply}
            disabled={!available || app.pending}
            title={available ? undefined : "当前目标无更新"}
          >
            {app.pending ? "更新中…" : "更新并重启"}
          </Button>
        </div>
      </div>

      <div className="rounded-md border border-border bg-muted px-3 py-2 text-xs text-muted-foreground">
        仅向前更新(ff-only),不支持回退——数据库结构只向前迁移。进入本页不自动检测,点「检查更新」才联网;本地改动仅在与更新内容冲突时被拒。
      </div>

      {!checked && !isError ? (
        <p className="text-sm text-muted-foreground">尚未检查 — 点击「检查更新」获取最新信息</p>
      ) : isError ? (
        <p className="text-sm text-destructive">检查失败:{errMsg(error)}</p>
      ) : st && !st.ok ? (
        <p className="text-sm text-destructive">检查失败:{st.error ?? "未知错误"}</p>
      ) : st ? (
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
            <InfoTile label="当前版本" value={st.current_version} valueClass="text-foreground" />
            <InfoTile label="当前提交" value={st.current_sha} valueClass="text-foreground" />
            <InfoTile
              label={mode === "tag" ? "最新版本" : "最新提交"}
              value={mode === "tag" ? (st.tag ?? "—") : (st.commit_sha ?? "—")}
              valueClass="text-foreground"
            />
          </div>
          {st.dirty && (
            <p className="text-xs text-warning">工作树有未提交改动,仅当与更新内容冲突时才会被拒</p>
          )}
          <StateNote st={st} mode={mode} />
        </div>
      ) : null}

      {app.error && (
        <div className="flex flex-wrap items-center gap-3 rounded-md border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive">
          <span>{app.error}</span>
          <Button size="sm" variant="ghost" onClick={() => window.location.reload()}>刷新页面</Button>
        </div>
      )}
      {app.updating && (
        <div className="rounded-md border border-border bg-muted p-3 text-sm text-muted-foreground">
          正在更新并重启,页面将在恢复后自动刷新…
        </div>
      )}
    </div>
  );
}
