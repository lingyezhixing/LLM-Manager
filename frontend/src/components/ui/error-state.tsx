import { Button } from "@/components/ui/button";

/** 加载失败 + 重试块(8 处站点共用;message 可空——站点无错误详情时只显示前缀)。 */
export function ErrorState({ message, onRetry, prefix = "加载失败", className = "" }: {
  message?: string;
  onRetry?: () => void;
  prefix?: string;
  className?: string;
}) {
  return (
    <div className={`flex items-center gap-2 text-sm text-destructive ${className}`}>
      <span>{prefix}{message ? `: ${message}` : ""}</span>
      {onRetry && <Button size="sm" variant="ghost" onClick={onRetry}>重试</Button>}
    </div>
  );
}
