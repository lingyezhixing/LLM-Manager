import {
  type ReactNode,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { createPortal } from "react-dom";
import { AlertCircle, CheckCircle2, Info, X } from "lucide-react";
import { ToastContext, type ToastApi } from "@/lib/toast";
import { usePresence } from "@/lib/hooks/use-presence";

// ---------- 命令式 toast:portal 到 body,右下堆叠,自动消失 ----------
// 与 ConfirmProvider 同范式(context + 单例队列 + portal)。
// 进场右滑、退场淡出(usePresence 驻留,播完才出队);
// TTL 与手动关闭同走 dying 态(先动画后移除,堆叠不错位)。
// 走语义 token(success/destructive),lucide 图标打包内联(离线安全)。
type ToastType = "success" | "error" | "info";

interface ToastItem {
  id: number;
  type: ToastType;
  message: string;
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

function ToastRow({ item, onGone }: { item: ToastItem; onGone: (id: number) => void }) {
  const Icon = ICONS[item.type];
  const [dying, setDying] = useState(false);
  const rowRef = useRef<HTMLDivElement>(null);

  const shown = usePresence(!dying, 120, rowRef);

  // TTL 自持:dying 前每行自武装,到期走与手动关闭相同的退场动画。
  useEffect(() => {
    if (dying) return;
    const t = setTimeout(() => setDying(true), TTL[item.type]);
    return () => clearTimeout(t);
  }, [dying, item.type]);

  // 退场驻留结束(presence 撤)→ 通知父级真正出队。
  useEffect(() => {
    if (!shown) onGone(item.id);
  }, [shown, item.id, onGone]);

  if (!shown) return null;

  return (
    <div
      ref={rowRef}
      data-toast-id={item.id}
      role={item.type === "error" ? "alert" : "status"}
      aria-live={item.type === "error" ? "assertive" : "polite"}
      className={`flex items-start gap-2 rounded-lg border border-border bg-popover p-3 text-sm text-popover-foreground shadow-card ${
        dying ? "animate-toast-out" : "animate-toast-in"
      }`}
    >
      <Icon className={`mt-0.5 h-4 w-4 shrink-0 ${ICON_CLS[item.type]}`} />
      <span className="flex-1 break-words">{item.message}</span>
      <button
        type="button"
        onClick={() => setDying(true)}
        aria-label="关闭"
        className="shrink-0 text-muted-foreground hover:text-foreground"
      >
        <X className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}

// FLIP:列表增删(新入场挤旧下行/移除上移)时,被移动的行以 transform 平滑过渡到新位置。
// 新行自身不做位移(它的入场由 keyframes 负责),仅对「上一轮已存在且 offsetTop 变化」的行补位。
function useFlipDeck(decks: unknown[], listRef: React.RefObject<HTMLDivElement | null>) {
  const prevTops = useRef<Map<number, number>>(new Map());
  useLayoutEffect(() => {
    const el = listRef.current;
    if (!el) return;
    const tops = new Map<number, number>();
    let moved = false;
    for (const child of el.children) {
      const elc = child as HTMLElement;
      const id = Number(elc.dataset.toastId);
      if (Number.isNaN(id)) continue;
      const top = elc.offsetTop;
      const prev = prevTops.current.get(id);
      if (prev !== undefined && prev !== top) {
        moved = true;
        elc.style.transform = `translateY(${prev - top}px)`;
        elc.style.transition = "none";
      }
      tops.set(id, top);
    }
    prevTops.current = tops;
    if (moved) {
      requestAnimationFrame(() => {
        for (const child of el.children) {
          const elc = child as HTMLElement;
          if (elc.style.transform) {
            elc.style.transition = "transform 240ms var(--motion-ease)";
            elc.style.transform = "";
          }
        }
      });
    }
  }, [decks, listRef]);
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const nextId = useRef(0);
  const deckRef = useRef<HTMLDivElement>(null);

  const push = useCallback((message: string, type: ToastType) => {
    setToasts((cur) => [...cur, { id: ++nextId.current, type, message }]);
  }, []);

  const api = useMemo<ToastApi>(
    () => ({
      success: (m) => push(m, "success"),
      error: (m) => push(m, "error"),
      info: (m) => push(m, "info"),
    }),
    [push],
  );

  const handleGone = useCallback((id: number) => {
    setToasts((cur) => cur.filter((t) => t.id !== id));
  }, []);

  useFlipDeck(toasts, deckRef);

  return (
    <ToastContext.Provider value={api}>
      {children}
      {createPortal(
        // 右上堆叠(flex-col-reverse:最新在顶,新入场把旧消息向下挤);
        // pointer-events-none 容器不挡点击,每条 toast 重新接管(关闭按钮)。
        <div
          ref={deckRef}
          className="pointer-events-none fixed right-4 top-24 z-[60] flex w-80 max-w-[calc(100vw-2rem)] flex-col-reverse gap-2"
        >
          {toasts.map((t) => (
            <div key={t.id} className="pointer-events-auto">
              <ToastRow item={t} onGone={handleGone} />
            </div>
          ))}
        </div>,
        document.body,
      )}
    </ToastContext.Provider>
  );
}
