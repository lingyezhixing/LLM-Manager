import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { ConfigSaveBar } from "@/components/config-save-bar";
import { useConfirm } from "@/lib/hooks/use-confirm";
import { ErrorState } from "@/components/ui/error-state";
import { Field, TextInput } from "@/components/ui/form";
import { useToast } from "@/lib/hooks/use-toast";
import type { WolConfig } from "@/lib/api";
import { useConfig, useDeleteWol, useSendWol, useUpdateWol } from "@/lib/hooks/use-config";

function shallowEqual(a: WolConfig, b: WolConfig): boolean {
  return a.broadcast_address === b.broadcast_address && a.mac_address === b.mac_address;
}

// 系统页「网络」区:WOL 唤醒配置(广播地址 + MAC)。纯配置,不测唤醒(托盘有菜单)。
export function WolPanel() {
  const { data, isLoading, isError, error, refetch } = useConfig();
  const update = useUpdateWol();
  const del = useDeleteWol();
  const send = useSendWol();
  const confirm = useConfirm();
  const toast = useToast();
  const serverWol: WolConfig = data?.wol ?? { broadcast_address: "", mac_address: "" };
  const [form, setForm] = useState<WolConfig>(serverWol);
  const syncedRef = useRef<WolConfig>(serverWol);
  const hasConfig = serverWol.broadcast_address !== "" || serverWol.mac_address !== "";

  // 外部刷新跟随:未编辑(与 synced 一致)才采纳,编辑中保留。
  useEffect(() => {
    const incoming: WolConfig = data?.wol ?? { broadcast_address: "", mac_address: "" };
    setForm((prev) => {
      if (!shallowEqual(prev, syncedRef.current)) return prev;   // 编辑中,保留
      syncedRef.current = incoming;
      return incoming;
    });
  }, [data?.wol]);

  if (isError) {
    return <ErrorState message={(error as Error).message} onRetry={() => refetch()} />;
  }
  if (isLoading) {
    return <div className="text-sm text-muted-foreground">加载中…</div>;
  }

  const dirty = !shallowEqual(form, syncedRef.current);
  const macOk = form.mac_address.trim() !== "";
  const bcastOk = form.broadcast_address.trim() !== "";   // B8:后端两字段均 min_length=1,前端同步门控
  const set = (k: keyof WolConfig, v: string) => setForm({ ...form, [k]: v });

  const onClear = async () => {
    const ok = await confirm({
      title: "还原网络唤醒配置?",
      description: "清除后需重新填写才能使用网络唤醒(托盘「网络唤醒」将提示未配置)。",
      confirmText: "还原",
      cancelText: "取消",
      danger: true,
    });
    if (!ok) return;
    del.mutate(undefined, {
      onSuccess: () => {
        const empty: WolConfig = { broadcast_address: "", mac_address: "" };
        syncedRef.current = empty;
        setForm(empty);
        toast.success("网络唤醒配置已还原");
      },
      onError: (e: unknown) => toast.error((e as Error).message),
    });
  };

  const onSend = () => {
    send.mutate(form, {
      onSuccess: () => toast.success("魔术包已发送"),
      onError: (e: unknown) => toast.error((e as Error).message),
    });
  };

  return (
    <div>
      <div className="grid grid-cols-1 gap-x-6 sm:grid-cols-2">
        <Field label="广播地址" hint="如 255.255.255.255" htmlFor="wol-bcast">
          <TextInput id="wol-bcast" value={form.broadcast_address} onChange={(e) => set("broadcast_address", e.target.value)} />
        </Field>
        <Field label="目标 MAC 地址" hint="如 aa:bb:cc:dd:ee:ff" htmlFor="wol-mac"
          error={!macOk ? "MAC 地址必填" : null}>
          <TextInput id="wol-mac" value={form.mac_address} onChange={(e) => set("mac_address", e.target.value)} />
        </Field>
      </div>
      {dirty && (
        <ConfigSaveBar
          saving={update.isPending}
          error={update.error ? (update.error as Error).message : null}
          onSave={() =>
            update.mutate(form, {
              onSuccess: () => {
                syncedRef.current = form;
                toast.success("网络唤醒配置已保存");
              },
            })
          }
          onReset={() => setForm(syncedRef.current)}
          saveDisabled={!macOk || !bcastOk}
        />
      )}
      {hasConfig && (
        <div className="mt-4 flex justify-end gap-2">
          <Button type="button" onClick={onSend} disabled={!macOk || !bcastOk || send.isPending}>
            {send.isPending ? "发送中…" : "发送魔术包"}
          </Button>
          <Button type="button" variant="destructive" onClick={onClear} disabled={del.isPending}>
            {del.isPending ? "还原中…" : "还原"}
          </Button>
        </div>
      )}
    </div>
  );
}
