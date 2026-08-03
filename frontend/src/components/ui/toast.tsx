import {
  type ReactNode,
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { createPortal } from "react-dom";
import { AlertCircle, CheckCircle2, Info, X } from "lucide-react";

// ---------- 命令式 toast:portal 到 body,右下堆叠,自动消失 ----------
// 与 ConfirmProvider 同范式(context + 单例队列 + portal)。无动效(遵设计原则),
// 走语义 token(success/destructive),lucide 图标打包内联(离线安全)。
type ToastType = "success" | "error" | "info";

interface ToastItem {
  id: number;
  type: ToastType;
  message: string;
}

interface ToastApi {
  success: (message: string) => void;
  error: (message: string) => void;
  info: (message: string) => void;
}

const ToastContext = createContext<ToastApi | null>(null);

/** 必须在 <ToastProvider> 内调用。success/error/info 各一。 */
export function useToast(): ToastApi {
  const api = useContext(ToastContext);
  if (!api) throw new Error("useToast 必须在 <ToastProvider> 内使用");
  return api;
}

// error 比 success/info 停留更久,留时间看清。
const TTL: Record<ToastType, number> = { success: 3200, info: 3200, error: 4500 };

const ICONS: Record<ToastType, typeof CheckCircle2> = {
  success: CheckCircle2,
  error: AlertCircle,
  info: Info,
};
const ICON_CLS: Record<ToastType, string> = {
  success: "text-success",
  error: "text-destructive",
  info: "text-foreground",
};

function ToastRow({ item, onClose }: { item: ToastItem; onClose: () => void }) {
  const Icon = ICONS[item.type];
  return (
    <div
      role={item.type === "error" ? "alert" : "status"}
      aria-live={item.type === "error" ? "assertive" : "polite"}
      className="flex items-start gap-2 rounded-md border border-border bg-popover p-3 text-sm text-popover-foreground shadow-lg"
    >
      <Icon className={`mt-0.5 h-4 w-4 shrink-0 ${ICON_CLS[item.type]}`} />
      <span className="flex-1 break-words">{item.message}</span>
      <button
        type="button"
        onClick={onClose}
        aria-label="关闭"
        className="shrink-0 text-muted-foreground hover:text-foreground"
      >
        <X className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const nextId = useRef(0);
  const timers = useRef(new Map<number, ReturnType<typeof setTimeout>>());

  const dismiss = useCallback((id: number) => {
    setToasts((cur) => cur.filter((t) => t.id !== id));
    const handle = timers.current.get(id);
    if (handle !== undefined) {
      clearTimeout(handle);
      timers.current.delete(id);
    }
  }, []);

  const push = useCallback(
    (message: string, type: ToastType) => {
      const id = ++nextId.current;
      setToasts((cur) => [...cur, { id, type, message }]);
      timers.current.set(id, setTimeout(() => dismiss(id), TTL[type]));
    },
    [dismiss],
  );

  const api = useMemo<ToastApi>(
    () => ({
      success: (m) => push(m, "success"),
      error: (m) => push(m, "error"),
      info: (m) => push(m, "info"),
    }),
    [push],
  );

  // 卸载时清掉所有未触发的定时器,防泄漏。
  useEffect(() => {
    const map = timers.current;
    return () => {
      map.forEach((h) => clearTimeout(h));
      map.clear();
    };
  }, []);

  return (
    <ToastContext.Provider value={api}>
      {children}
      {createPortal(
        // 容器 pointer-events-none:不挡右下角空区;每条 toast 重新接管点击(关按钮)。
        <div className="pointer-events-none fixed bottom-4 right-4 z-[60] flex w-80 max-w-[calc(100vw-2rem)] flex-col gap-2">
          {toasts.map((t) => (
            <div key={t.id} className="pointer-events-auto">
              <ToastRow item={t} onClose={() => dismiss(t.id)} />
            </div>
          ))}
        </div>,
        document.body,
      )}
    </ToastContext.Provider>
  );
}
