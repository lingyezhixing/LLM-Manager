import { useContext } from "react";
import { ToastContext, type ToastApi } from "@/lib/toast";

/** 必须在 <ToastProvider> 内调用。success/error/info 各一。 */
export function useToast(): ToastApi {
  const api = useContext(ToastContext);
  if (!api) throw new Error("useToast 必须在 <ToastProvider> 内使用");
  return api;
}
