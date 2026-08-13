import { useState } from "react";
import { useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import {
  applyUpdate,
  checkUpdate,
  fetchConfig,
  fetchHealth,
  fetchRestartStatus,
  fetchSystemInfo,
  fetchUpdateStatus,
  restartApp,
  updateLogRetention,
  updateProgram,
  type LogRetention,
  type ProgramUpdate,
  type UpdateStatus,
  type UpdateTarget,
} from "@/lib/api";
import { errMsg } from "@/lib/format";

// 配置写回后失效:config(读穿取新值)+ restart-status(顶部横幅按新状态刷新)。
// 供 useUpdateProgram/useUpdateWol/useUpdateClaudeConfigs 共用;日志保留/应用预设语义不同,各管各的。
export function invalidateConfig(qc: QueryClient) {
  qc.invalidateQueries({ queryKey: ["config"] });
  qc.invalidateQueries({ queryKey: ["restart-status"] });
}

export function useSystemInfo() {
  return useQuery({ queryKey: ["system", "info"], queryFn: fetchSystemInfo });
}

export function useConfig() {
  return useQuery({ queryKey: ["config"], queryFn: fetchConfig });
}

export function useRestartStatus() {
  return useQuery({ queryKey: ["restart-status"], queryFn: fetchRestartStatus });
}

// 项目首个 useMutation:写后失效 config + restart-status,横幅按新状态刷新。
export function useUpdateProgram() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ProgramUpdate) => updateProgram(body),
    onSuccess: () => invalidateConfig(qc),
  });
}

// 日志保留规则已并入 AppConfig 快照(retention_from_store 每轮读 fresh,即时生效);
// 非 _RESTART_FIELDS → 恒不触发重启(无需失效 restart-status);失效 config(其 logs 字段 get_setting 直读)。
export function useUpdateLogRetention() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: LogRetention) => updateLogRetention(body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["config"] });
    },
  });
}

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

// 重连两阶段:① 最多 ~10s 等一次 /health 失败(确认旧进程已停、端口释放,防假阳性);
// ② 等首次成功(新进程已起),硬超时 timeoutMs。超时 → reject。
async function awaitReconnect(timeoutMs: number): Promise<void> {
  const hardDeadline = Date.now() + timeoutMs;
  const phase1Deadline = Date.now() + 10_000;
  while (Date.now() < phase1Deadline) {
    try {
      await fetchHealth();
    } catch {
      break;          // 观察到失败 → 进阶段 2
    }
    await sleep(500);
  }
  while (Date.now() < hardDeadline) {
    await sleep(1000);
    try {
      await fetchHealth();
      return;         // 新进程已起
    } catch {
      // 仍下线,继续等
    }
  }
  throw new Error("timeout");
}

// 自重启流程:触发后端关闭(202)→ poll /health 两阶段重连 → 恢复后整页 reload 反映新状态。
// /api/config/restart(配置字段重启)与 /api/update/apply(自更新重启)共用同一流程。
function useReconnectReload<T>(action: (arg: T) => Promise<unknown>) {
  const [restarting, setRestarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const mutate = useMutation({
    mutationFn: (arg: T) => action(arg),
    onSuccess: async () => {
      setRestarting(true);
      setError(null);
      try {
        await awaitReconnect(60_000);
        window.location.reload();   // 整页 reload 自然带回新配置/新代码,无需先 invalidate
      } catch {
        setError("重启超时,请手动检查后刷新页面。");
        setRestarting(false);
      }
    },
    onError: (e: unknown) => setError(errMsg(e)),
  });
  return { trigger: (arg: T) => mutate.mutate(arg), restarting, pending: mutate.isPending, error };
}

export function useRestartApp() {
  const r = useReconnectReload(() => restartApp());
  return { triggerRestart: () => r.trigger(undefined), restarting: r.restarting, pending: r.pending, error: r.error };
}

// 自更新:POST /api/update/apply(target 细粒度)→ 成功后走同一重连流程。
export function useUpdateApp() {
  const r = useReconnectReload((target: UpdateTarget) => applyUpdate(target));
  return { triggerUpdate: (target: UpdateTarget) => r.trigger(target), updating: r.restarting, pending: r.pending, error: r.error };
}

export function useUpdateStatus() {
  // 只读后端启动检测缓存:GET /status 无网络,刷新/进页不触发检测;失败不自动重试。
  // 启动检测未完成(checking=true)时短轮询等待结果——这是等「程序启动时那一次检测」,
  // 不新增任何检测;完成后轮询自动停止。
  return useQuery({
    queryKey: ["update", "status"],
    queryFn: fetchUpdateStatus,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    retry: false,
    refetchInterval: (query) => {
      const d = query.state.data as UpdateStatus | undefined;
      return d?.checking ? 1500 : false;
    },
  });
}

// 手动检查更新(POST /api/update/check,唯一触发后端重新检测的入口)。
export function useUpdateCheck() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: checkUpdate,
    onSuccess: (data) => qc.setQueryData(["update", "status"], data),
  });
}
