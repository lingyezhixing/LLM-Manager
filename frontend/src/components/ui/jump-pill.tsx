/** 滚动区「跳到底部」药丸:全站统一外观与位置(relative 容器内绝对定位右下)。
 *  显隐与文案由调用方决定——统一约定「不在底部就显示」;文案如
 *  `↓ N 条新日志` / `↓ 回到底部` / `← 返回最新 (N)`。 */
export function JumpPill({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="animate-swap-in absolute bottom-3 right-3 z-20 rounded-full border border-primary-accent/50 bg-primary-accent/20 px-2.5 py-1 text-dense text-foreground shadow-card transition-colors duration-(--motion-fast) hover:bg-primary-accent/30"
    >
      {label}
    </button>
  );
}
