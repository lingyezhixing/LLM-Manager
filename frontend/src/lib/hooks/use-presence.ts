import { useEffect, useState } from "react";

/** 退场驻留:open=true → 立即 shown;open 变 false → shown 保持到退场动画播完再撤。
 *  Web Animations API 等待(animationend 在元素即将卸载/display:none 下不可靠,WAAPI 稳);
 *  exitMs+50ms 兜底(无动画/被跳过时也能撤)。StrictMode 双挂载安全(effect 幂等)。
 *  用法:const ref = useRef<HTMLDivElement>(null); const shown = usePresence(open, 120, ref);
 *  根节点按 open 切 enter/exit 类,shown 控制是否渲染。 */
export function usePresence(
  open: boolean,
  exitMs: number,
  ref: React.RefObject<HTMLElement | null>,
): boolean {
  const [shown, setShown] = useState(open);
  useEffect(() => {
    if (open) {
      setShown(true);
      return;
    }
    // 世代守卫:退场期间快速重开(open false→true)时,前一轮退场回调不得把 shown 撤回 false
    let stale = false;
    const el = ref.current;
    const anims = el?.getAnimations() ?? [];
    const done = () => { if (!stale) setShown(false); };
    const timer = setTimeout(done, exitMs + 50); // 兜底:无动画/被跳过也能撤
    if (anims.length > 0) {
      Promise.all(anims.map((a) => a.finished)).then(done, done);
    }
    return () => { stale = true; clearTimeout(timer); };
  }, [open, exitMs, ref]);
  return shown;
}
