import {
  type ReactNode,
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";
import { createPortal } from "react-dom";
import { Button } from "@/components/ui/button";

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

  useEffect(() => {
    if (!open) return;
    const previouslyFocused = document.activeElement as HTMLElement | null;

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
      previouslyFocused?.focus?.();
    };
  }, [open, onClose]);

  if (!open) return null;
  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-background/80 p-4 backdrop-blur-sm"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();   // 仅点遮罩本体才关(面板内点击不关)
      }}
      role="dialog"
      aria-modal="true"
      aria-labelledby={labelledBy}
    >
      <div
        ref={panelRef}
        className="w-full max-w-sm rounded-lg border border-border bg-popover p-5 text-popover-foreground shadow-lg"
      >
        {children}
      </div>
    </div>,
    document.body,
  );
}

// ---------- 命令式 confirm ----------
interface ConfirmOptions {
  title: string;
  description?: string;
  confirmText?: string;
  cancelText?: string;
  danger?: boolean;
}
type ConfirmFn = (opts: ConfirmOptions) => Promise<boolean>;

const ConfirmContext = createContext<ConfirmFn | null>(null);

/** 必须在 <ConfirmProvider> 内调用。返回 true=确认,false=取消/Esc/点外。 */
export function useConfirm(): ConfirmFn {
  const fn = useContext(ConfirmContext);
  if (!fn) throw new Error("useConfirm 必须在 <ConfirmProvider> 内使用");
  return fn;
}

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
      <h2 id="confirm-title" className="text-sm font-semibold text-foreground">{opts.title}</h2>
      {opts.description && (
        <p className="mt-2 text-sm text-muted-foreground">{opts.description}</p>
      )}
      <div className="mt-5 flex justify-end gap-2">
        <Button variant="ghost" size="sm" onClick={onCancel}>{opts.cancelText ?? "取消"}</Button>
        <Button variant={opts.danger ? "destructive" : "default"} size="sm" onClick={onConfirm}>
          {opts.confirmText ?? "确认"}
        </Button>
      </div>
    </>
  );
}

/** 单例:同时只渲染一个确认框。第二次 confirm() 排队,等当前 resolve 后显示。 */
export function ConfirmProvider({ children }: { children: ReactNode }) {
  const queue = useRef<Pending[]>([]);
  const [shown, setShown] = useState<Pending | null>(null);

  const confirm = useCallback<ConfirmFn>((opts) => {
    return new Promise<boolean>((resolve) => {
      queue.current.push({ opts, resolve });
      setShown((cur) => cur ?? queue.current.shift() ?? null);
    });
  }, []);

  const settle = useCallback((ok: boolean) => {
    setShown((cur) => {
      cur?.resolve(ok);
      return queue.current.shift() ?? null;
    });
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
