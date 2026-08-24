import {
  type ReactNode,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import { createPortal } from "react-dom";
import { AlertTriangle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ConfirmContext, type ConfirmFn, type ConfirmOptions } from "@/lib/confirm";
import { usePresence } from "@/lib/hooks/use-presence";
import { APP_SCROLL_ROOT_ID } from "@/components/app-shell";

// ---------- 底层 Dialog:portal + Esc + 点遮罩关 + focus trap + 焦点还原 + aria ----------
function Dialog({
  open, onClose, children, labelledBy,
}: {
  open: boolean;
  onClose: () => void;
  children: ReactNode;
  labelledBy?: string;
}) {
  const panelRef = useRef<HTMLDivElement>(null);
  const shown = usePresence(open, 120, panelRef);

  useEffect(() => {
    if (!open) return;
    const previouslyFocused = document.activeElement as HTMLElement | null;
    // 背景滚动锁:body 不滚(壳层 h-screen overflow-hidden),真正滚动的是壳层内层容器;
    // HTML 属性改名(overflow-y)须内联还原,否则与 Tailwind 类冲突(类在 style 后仍生效)。
    const scroller = document.getElementById(APP_SCROLL_ROOT_ID);
    if (scroller) scroller.style.overflowY = "hidden";

    const focusable = () =>
      panelRef.current?.querySelectorAll<HTMLElement>(
        'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
      ) ?? [];

    // 初始焦点入面板(首个可聚焦元素)
    focusable()[0]?.focus();

    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
        return;
      }
      if (e.key !== "Tab") return;
      const items = focusable();
      if (items.length === 0) return;
      const first = items[0];
      const last = items[items.length - 1];
      const active = document.activeElement;
      if (e.shiftKey && active === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && active === last) {
        e.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      if (scroller) scroller.style.overflowY = "";
      previouslyFocused?.focus?.();
    };
  }, [open, onClose]);

  if (!shown) return null;
  return createPortal(
    <div
      className={`fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4 backdrop-blur-sm ${open ? "animate-dialog-in" : "animate-dialog-out"}`}
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();   // 仅点遮罩本体才关(面板内点击不关)
      }}
      role="dialog"
      aria-modal="true"
      aria-labelledby={labelledBy}
    >
      <div
        ref={panelRef}
        className={`w-full max-w-sm rounded-xl border border-border bg-popover p-5 text-popover-foreground shadow-card ${open ? "animate-panel-in" : "animate-panel-out"}`}
      >
        {children}
      </div>
    </div>,
    document.body,
  );
}

// ---------- 命令式 confirm ----------
interface Pending {
  opts: ConfirmOptions;
  resolve: (ok: boolean) => void;
}

function ConfirmBody({ opts, onConfirm, onCancel }: {
  opts: ConfirmOptions;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <>
      <div className="flex items-start gap-2.5">
        {opts.danger && <AlertTriangle aria-hidden className="mt-0.5 h-4 w-4 shrink-0 text-destructive" />}
        <div className="min-w-0">
          <h2 id="confirm-title" className="text-sm font-semibold text-foreground">{opts.title}</h2>
          {opts.description && (
            <p className="mt-2 text-sm text-muted-foreground">{opts.description}</p>
          )}
        </div>
      </div>
      <div className="mt-5 flex justify-end gap-2">
        <Button variant="ghost" size="sm" onClick={onCancel}>{opts.cancelText ?? "取消"}</Button>
        <Button variant={opts.danger ? "destructive" : "default"} size="sm" onClick={onConfirm}>
          {opts.confirmText ?? "确认"}
        </Button>
      </div>
    </>
  );
}

/** 单例:同时只渲染一个确认框。第二次 confirm() 排队,等当前 resolve 后显示。
 * 不变量:shown === (queue[0] ?? null) 恒成立——据此可读 queue[0] 作当前项。
 * setState updater 必须纯(只读 queue,无 shift/resolve 副作用):React dev StrictMode
 * 双调用 updater 并取第二次返回值,旧代码在 updater 内 shift() 会丢队首、resolve 永不触发
 * → 删除/清除等 confirm 流在 dev 下永久挂起(F2)。副作用移到 callback body(仅跑一次)。 */
export function ConfirmProvider({ children }: { children: ReactNode }) {
  const queue = useRef<Pending[]>([]);
  const [shown, setShown] = useState<Pending | null>(null);

  const confirm = useCallback<ConfirmFn>((opts) => {
    return new Promise<boolean>((resolve) => {
      queue.current.push({ opts, resolve });
      // updater 纯读:当前无显示项时推进队首(刚 push 的项 = queue[0])。
      setShown((cur) => cur ?? queue.current[0] ?? null);
    });
  }, []);

  const settle = useCallback((ok: boolean) => {
    // 副作用(resolve + 出队)在 callback body 内执行一次,不进 updater。
    const cur = queue.current[0];
    if (cur) {
      queue.current.shift();
      cur.resolve(ok);
    }
    setShown(queue.current[0] ?? null);
  }, []);

  return (
    <ConfirmContext.Provider value={confirm}>
      {children}
      <Dialog open={shown !== null} onClose={() => settle(false)} labelledBy="confirm-title">
        {shown && (
          <ConfirmBody
            opts={shown.opts}
            onConfirm={() => settle(true)}
            onCancel={() => settle(false)}
          />
        )}
      </Dialog>
    </ConfirmContext.Provider>
  );
}
