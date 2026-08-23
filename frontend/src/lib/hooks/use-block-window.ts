import { useCallback, useEffect, useRef, useState } from "react";

/** 块级虚拟窗口:长列表按 BLOCK 行分块,IntersectionObserver 只挂载可视块±BUFFER,
 *  块外用已测高度(首测前按 EST_ROW_H 估算)占位——日志行可换行(变高),不能用等高虚拟化。
 *  mount(b) 强制挂载某块(搜索跳转目标块不可见时)。 */
const BLOCK = 120;
const BUFFER = 1;
const EST_ROW_H = 20;

/** 行号 → 块号(纯函数,身份稳定,消费方可安全放 effect 依赖)。 */
export const blockOf = (lineIdx: number) => Math.floor(lineIdx / BLOCK);

export function useBlockWindow(
  total: number,
  scrollRef: React.RefObject<HTMLElement | null>,
) {
  const [visible, setVisible] = useState<Set<number>>(new Set([0]));
  const heights = useRef<Map<number, number>>(new Map());
  const forced = useRef<Set<number>>(new Set());

  const mount = useCallback((b: number) => {
    forced.current.add(b);
    setVisible((prev) => (prev.has(b) ? prev : new Set(prev).add(b)));
  }, []);

  useEffect(() => {
    const root = scrollRef.current;
    if (!root) return;
    const io = new IntersectionObserver(
      (entries) => {
        setVisible((prev) => {
          let next: Set<number> | null = null;
          for (const en of entries) {
            const b = Number((en.target as HTMLElement).dataset.block);
            if (Number.isNaN(b)) continue;
            if (en.isIntersecting) {
              for (let d = -BUFFER; d <= BUFFER; d++) forced.current.add(b + d);
            }
            const want = en.isIntersecting || forced.current.has(b);
            const has = prev.has(b);
            if (want !== has) {
              next ??= new Set(prev);
              if (want) next.add(b); else next.delete(b);
            }
          }
          return next ?? prev;
        });
      },
      { root, rootMargin: "600px 0px" },
    );
    root.querySelectorAll<HTMLElement>("[data-block]").forEach((el) => io.observe(el));
    return () => io.disconnect();
    // #8 依赖 [visible](而非无依赖数组):块列表是「占位+真实行」全量渲染且 key 稳定,
    // 无需随每帧渲染重建 observer;visible 变化时重连即捕获新挂载块(真实块替换占位块
    // 复用同一 DOM 节点,搜索跳转 mount(b) 亦经 setVisible → 重连兜住)。
  }, [visible, scrollRef]);

  const nBlocks = Math.ceil(total / BLOCK);

  return { visible, blockOf, mount, nBlocks, BLOCK, heights, EST_ROW_H };
}
// blockOf 已提升为模块级导出(见上)
