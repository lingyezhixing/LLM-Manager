// 自更新面板:git 标签版本身份 + 严格 ff-only(仅向前,无回退 / 无版本选择 / 无提交浏览)。
// 更新目标两个细粒度:tag = 最新标签(稳定发布)、commit = 最新提交(前沿)。
// 本地未提交改动不预拒——仅与更新内容冲突时后端拒绝;网络仅用户显式触发(打开本页/检查)。
import { Button } from "@/components/ui/button";
import { Loading } from "@/components/ui/card";
import { ErrorState } from "@/components/ui/error-state";
import { InfoTile } from "@/components/ui/info-tile";
import type { UpdateStatus, UpdateTarget } from "@/lib/api";
import { errMsg } from "@/lib/format";
import { useConfirm } from "@/lib/hooks/use-confirm";
import { useToast } from "@/lib/hooks/use-toast";
import { useUpdateApp, useUpdateStatus } from "@/lib/hooks/use-config";

function StateNote({ st }: { st: UpdateStatus }) {
  if (!st.ok) return <p className="text-sm text-destructive">检查失败:{st.error ?? "未知错误"}</p>;
  if (st.conflicted) return <p className="text-sm text-warning">本地与远端历史分叉,无法更新,需手动处理</p>;
  if (st.dirty) return <p className="text-sm text-warning">工作树有未提交改动,仅当与更新内容冲突时才会被拒</p>;
  if (!st.tag_available && !st.commit_available) return <p className="text-sm text-success">已是最新版本</p>;
  return <p className="text-sm text-success">检测到新版本,可更新</p>;
}

export function UpdatePanel() {
  const { data, isLoading, isError, error, refetch, isFetching } = useUpdateStatus();
  const app = useUpdateApp();
  const confirm = useConfirm();
  const toast = useToast();

  if (isError) return <ErrorState message={errMsg(error)} onRetry={() => refetch()} />;
  if (isLoading) return <Loading />;
  const st = data!;

  const onApply = async (target: UpdateTarget) => {
    const label = target === "tag" ? (st.tag ? `最新标签 ${st.tag}` : "最新标签") : `最新提交 ${st.commit_sha ?? ""}`;
    const ok = await confirm({
      title: `${label} 更新并重启?`,
      description: "将拉取最新代码并重启程序,期间正在服务的模型会中断。仅向前更新,不支持回退;与本地未提交改动冲突时会被拒绝。",
      confirmText: "更新并重启",
      cancelText: "取消",
      danger: true,
    });
    if (!ok) return;
    app.triggerUpdate(target);
    toast.success("更新已开始,页面将在恢复后自动刷新");
  };

  return (
    <div>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
        <InfoTile label="当前版本" value={st.current_version} valueClass="text-foreground" />
        <InfoTile label="当前提交" value={st.current_sha} valueClass="text-foreground" />
        <InfoTile label="最新标签" value={st.tag ?? "—"} valueClass="text-foreground" />
      </div>

      <div className="mt-4 rounded-md border border-border bg-muted px-3 py-2 text-xs text-muted-foreground">
        仅支持向前更新(ff-only),不支持回退——数据库结构只向前迁移。更新前需联网;本地有未提交改动时不预拒,仅与更新内容冲突才会被拒。
      </div>

      <div className="mt-3"><StateNote st={st} /></div>

      {app.error && (
        <div className="mt-3 flex flex-wrap items-center gap-3 rounded-md border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive">
          <span>{app.error}</span>
          <Button size="sm" variant="ghost" onClick={() => window.location.reload()}>刷新页面</Button>
        </div>
      )}
      {app.updating && (
        <div className="mt-3 rounded-md border border-border bg-muted p-3 text-sm text-muted-foreground">
          正在更新并重启,页面将在恢复后自动刷新…
        </div>
      )}

      <div className="mt-4 flex flex-wrap justify-end gap-2">
        <Button type="button" variant="outline" onClick={() => refetch()} disabled={isFetching || app.pending}>
          {isFetching ? "检查中…" : "检查更新"}
        </Button>
        <Button
          type="button"
          variant="destructive"
          onClick={() => onApply("tag")}
          disabled={!st.tag_available || app.pending}
          title={st.tag_available ? undefined : "最新标签不可更新(无标签或已在其上)"}
        >
          {app.pending ? "更新中…" : `更新到最新标签${st.tag ? ` ${st.tag}` : ""}`}
        </Button>
        <Button
          type="button"
          variant="destructive"
          onClick={() => onApply("commit")}
          disabled={!st.commit_available || app.pending}
          title={st.commit_available ? undefined : "最新提交不可更新(已是最新或历史分叉)"}
        >
          {app.pending ? "更新中…" : `更新到最新提交${st.commit_sha ? ` ${st.commit_sha}` : ""}`}
        </Button>
      </div>
    </div>
  );
}
