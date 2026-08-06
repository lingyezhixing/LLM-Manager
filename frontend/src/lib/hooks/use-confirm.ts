import { useContext } from "react";
import { ConfirmContext, type ConfirmOptions } from "@/lib/confirm";

/** 必须在 <ConfirmProvider> 内调用。返回 true=确认,false=取消/Esc/点外。 */
export function useConfirm(): (opts: ConfirmOptions) => Promise<boolean> {
  const fn = useContext(ConfirmContext);
  if (!fn) throw new Error("useConfirm 必须在 <ConfirmProvider> 内使用");
  return fn;
}
