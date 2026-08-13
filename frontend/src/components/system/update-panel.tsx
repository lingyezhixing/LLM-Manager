// 自更新面板:git 标签版本身份 + 严格 ff-only。检查=fetch 对比(仅读 refs,不动工作树);
// 应用=拉取+合并+自动重启。版本以 git 标签为唯一事实源;网络仅用户显式触发
// (打开本页/点「检查更新」),后台永不自动,保持离线优先。
import { Button } from "@/components/ui/button";
import { Loading } from "@/components/ui/card";
import { ErrorState } from "@/components/ui/error-state";
import { InfoTile } from "@/components/ui/info-tile";
import type { UpdateStatus } from "@/lib/api";
import { errMsg } from "@/lib/format";
import { useConfirm } from "@/lib/hooks/use-confirm";
import { useToast } from "@/lib/hooks/use-toast";
import { useUpdateApp, useUpdateStatus } from "@/lib/hooks/use-config";

function StateNote({ st }: { st: UpdateStatus }) {
  if (!st.ok) return <p className="text-sm text-destructive">检查失败:{st.error ?? "未知错误"}</p>;
  if (st.dirty) return <p className="text-sm text-warning">工作树有未提交改动,更新前请先提交或还原</p>;
  if (st.conflicted) return <p className="text-sm text-warning">本地与远端历史分叉,无法快速前进更新,需手动处理</p>;
  if (st.up_to_date) return <p className="text-sm text-success">已是最新版本</p>;
  return (
    <p className="text-sm text-success">
      发现新版本 {st.latest_version ?? "未知"} — {st.commits_behind} 个新提交
    </p>
  );
}

export function UpdatePanel() {
  const { data, isLoading, isError, error, refetch, isFetching } = useUpdateStatus();
  const app = useUpdateApp();
  const confirm = useConfirm();
  const toast = useToast();

  if (isError) return <ErrorState message={errMsg(error)} onRetry={() => refetch()} />;
  if (isLoading) return <Loading />;
  const st = data!;

  const onApply = async () => {
    const ok = await confirm({
      title: "更新并重启?",
      description: st.latest_version
        ? `将拉取最新代码(${st.latest_version})并重启程序,期间正在服务的模型会中断。`
        : "将拉取最新代码并重启程序,期间正在服务的模型会中断。",
      confirmText: "更新并重启",
      cancelText: "取消",
      danger: true,
    });
    if (!ok) return;
    app.triggerUpdate();
    toast.success("更新已开始,页面将在恢复后自动刷新");
  };

  return (
    <div>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
        <InfoTile label="当前版本" value={st.current_version} valueClass="text-foreground" />
        <InfoTile label="最新版本" value={st.latest_version ?? "—"} valueClass="text-foreground" />
        <InfoTile label="当前提交" value={st.current_sha} valueClass="text-foreground" />
      </div>

      <div className="mt-4 rounded-md border border-border bg-muted px-3 py-2 text-xs text-muted-foreground">
        版本以 git 标签为准;更新仅快速前进(ff-only),工作树有未提交改动或历史分叉会被拒绝。
        检查需联网,更新后程序自动重启。
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

      <div className="mt-4 flex justify-end gap-2">
        <Button type="button" variant="outline" onClick={() => refetch()} disabled={isFetching || app.pending}>
          {isFetching ? "检查中…" : "检查更新"}
        </Button>
        <Button
          type="button"
          variant="destructive"
          onClick={onApply}
          disabled={!st.available || st.dirty || st.conflicted || app.pending}
        >
          {app.pending ? "更新中…" : "更新并重启"}
        </Button>
      </div>
    </div>
  );
}
