import { Button } from "@/components/ui/button";
import { useConfirm } from "@/components/ui/dialog";
import { useToast } from "@/components/ui/toast";
import { useDeleteModelData, useOrphanedModels, useStorageStats } from "@/lib/use-data";

// 数据库管理页(迁移 legacy DataManagement):存储统计 + 孤立模型清理 + 模型数据详情。
// 每载入获取一次(refetchOnMount: "always"),不轮询。删除 = 级联清数据 + 自动 VACUUM(后端)。
function formatBytes(n: number | null): string {
  if (n == null) return "—";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)} MB`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)} KB`;
  return `${n} B`;
}

function StatTile({ label, value, danger }: { label: string; value: string; danger?: boolean }) {
  return (
    <div className="rounded-lg border border-border px-3 py-2">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className={`mt-0.5 break-all text-base font-semibold ${danger ? "text-destructive" : "text-foreground"}`}>
        {value}
      </div>
    </div>
  );
}

export function DatabasePanel() {
  const stats = useStorageStats();
  const orphaned = useOrphanedModels();
  const del = useDeleteModelData();
  const confirm = useConfirm();
  const toast = useToast();

  const s = stats.data;
  const orphans = orphaned.data?.orphaned_models ?? [];

  const onDelete = async (name: string) => {
    const ok = await confirm({
      title: `删除模型「${name}」的全部数据?`,
      description: "请求记录与运行时间将一并删除,此操作不可撤销。",
      confirmText: "删除",
      cancelText: "取消",
      danger: true,
    });
    if (!ok) return;
    del.mutate(name, {
      onSuccess: () => toast.success(`已删除「${name}」的数据`),
      onError: (e: unknown) => toast.error((e as Error).message),
    });
  };

  if (stats.isError || orphaned.isError) {
    return (
      <div className="flex items-center gap-2 text-sm text-destructive">
        加载失败:{((stats.error ?? orphaned.error) as Error).message}
        <Button size="sm" variant="ghost" onClick={() => { stats.refetch(); orphaned.refetch(); }}>重试</Button>
      </div>
    );
  }
  if (stats.isLoading || orphaned.isLoading || !s) {
    return <div className="text-sm text-muted-foreground">加载中…</div>;
  }

  const entries = Object.entries(s.models_data).sort((a, b) => b[1].request_count - a[1].request_count);

  return (
    <div className="flex flex-col gap-6">
      {/* 存储统计 */}
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <StatTile label="数据库大小" value={formatBytes(s.size_bytes)} />
        <StatTile label="有数据模型数" value={String(s.total_models_with_data)} />
        <StatTile label="总请求数" value={s.total_requests.toLocaleString()} />
        <StatTile label="孤立模型数" value={String(orphans.length)} danger={orphans.length > 0} />
      </div>

      {/* 孤立模型管理 */}
      <div className="rounded-lg border border-warning/40 bg-warning/5 p-3">
        <div className="text-sm font-medium text-foreground">
          {orphans.length > 0 ? `发现 ${orphans.length} 个孤立模型` : "✓ 没有发现孤立模型"}
        </div>
        {orphans.length > 0 && (
          <ul className="mt-2 flex flex-col gap-2">
            {orphans.map((name) => (
              <li key={name} className="flex flex-wrap items-center gap-2 rounded-md border border-border bg-background px-3 py-2">
                <div className="min-w-0 flex-1">
                  <div className="text-sm font-medium text-foreground">{name}</div>
                  <div className="text-xs text-muted-foreground">此模型不在当前配置中,但数据库中存在数据</div>
                </div>
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  className="text-destructive"
                  onClick={() => onDelete(name)}
                  disabled={del.isPending}
                >
                  {del.isPending ? "删除中…" : "删除数据"}
                </Button>
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* 模型数据详情 */}
      <div>
        <div className="mb-2 text-sm font-medium text-foreground">模型数据详情</div>
        {entries.length === 0 ? (
          <div className="rounded-lg border border-dashed border-border p-8 text-center text-sm text-muted-foreground">
            暂无模型数据
          </div>
        ) : (
          <div className="overflow-hidden rounded-lg border border-border">
            <div className="grid grid-cols-[minmax(0,1fr)_auto_auto] gap-3 bg-muted px-3 py-2 text-xs font-medium text-muted-foreground">
              <div>模型名称</div>
              <div className="w-20 text-right">请求数量</div>
              <div className="w-20 text-right">运行数据</div>
            </div>
            {entries.map(([name, st]) => (
              <div key={name} className="grid grid-cols-[minmax(0,1fr)_auto_auto] gap-3 border-t border-border px-3 py-2 text-sm">
                <div className="flex min-w-0 items-center gap-1.5">
                  {orphans.includes(name) && (
                    <span className="shrink-0 rounded border border-destructive/60 px-1 text-[10px] leading-4 text-destructive">孤立</span>
                  )}
                  <span className="truncate text-foreground">{name}</span>
                </div>
                <div className="w-20 text-right text-muted-foreground">{st.request_count.toLocaleString()}</div>
                <div className={`w-20 text-right ${st.has_runtime_data ? "text-success" : "text-muted-foreground"}`}>
                  {st.has_runtime_data ? "✓ 有" : "✗ 无"}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
