// 未建区(模型/网络/Claude/日志)的统一占位。
export function ZonePlaceholder({ label }: { label: string }) {
  return (
    <div className="rounded-lg border border-dashed border-border p-16 text-center text-sm text-muted-foreground">
      {label} · 建设中（计划于后续阶段）
    </div>
  );
}
