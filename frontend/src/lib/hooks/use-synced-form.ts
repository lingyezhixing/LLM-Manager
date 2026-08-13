import { useEffect, useRef, useState } from "react";
import type { Dispatch, SetStateAction } from "react";

// 服务端快照 → 本地表单 的同步抽象(AGENTS.md §9-S5)。收敛 general/wol/claude-path/
// model-def 四处手写变体,固化契约:
// - 外部刷新(serverValue 变化)仅在「表单未编辑(form == baseline)」时跟随;
// - baseline 只在保存成功时推进(commit/advance),失败不丢 dirty(F1);
// - dirty = form != baseline(alwaysDirty 用于创建态恒脏)。
// 实现细节:baseline 存 state(dirty 依赖它反应式更新)+ ref 镜像(回调里读最新值),
// form 用 ref 镜像给跟随 effect 读,避免把 form 加进依赖导致编辑中反复触发。

export function useSyncedForm<T>(
  serverValue: T | null | undefined,
  initial: T,
  isEqual: (a: T, b: T) => boolean,
  opts: { alwaysDirty?: boolean } = {},
): {
  form: T;
  setForm: Dispatch<SetStateAction<T>>;
  dirty: boolean;
  baseline: T;
  /** 仅推进 baseline,表单不动(局部字段保存成功后合并)。 */
  advance: (next: T | ((base: T) => T)) => void;
  /** 推进 baseline 且将表单同步为 next(全量保存)。 */
  commit: (next: T) => void;
  /** 还原到最近 baseline。 */
  reset: () => void;
} {
  const [form, setForm] = useState<T>(initial);
  const [baseline, setBaseline] = useState<T>(initial);
  const baselineRef = useRef(baseline);
  const formRef = useRef(form);

  // 两镜像在每次 commit 后同步(声明在跟随 effect 之前,保证同一提交内先刷新再判定)。
  useEffect(() => {
    baselineRef.current = baseline;
  }, [baseline]);
  useEffect(() => {
    formRef.current = form;
  }, [form]);

  // 外部刷新跟随:未编辑(form==baseline)且服务端值确有变化时才采纳。
  // isEqual 为调用方模块级稳定函数(不作为依赖变化源)。
  useEffect(() => {
    if (serverValue === null || serverValue === undefined) return;
    const base = baselineRef.current;
    if (!isEqual(formRef.current, base)) return; // 编辑中,保留
    if (isEqual(base, serverValue)) return; // 值未变(引用刷新),不动
    baselineRef.current = serverValue;
    setBaseline(serverValue);
    setForm(serverValue);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [serverValue]);

  const dirty = (opts.alwaysDirty ?? false) || !isEqual(form, baseline);

  const advance = (next: T | ((base: T) => T)) => {
    setBaseline((base) => {
      const updated = typeof next === "function" ? (next as (b: T) => T)(base) : next;
      baselineRef.current = updated;
      return updated;
    });
  };

  const commit = (next: T) => {
    baselineRef.current = next;
    setBaseline(next);
    setForm(next);
  };

  const reset = () => setForm(baseline);

  return { form, setForm, dirty, baseline, advance, commit, reset };
}
