import { useState } from "react";
import { useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import {
  fetchConfig,
  fetchHealth,
  fetchRestartStatus,
  fetchSystemInfo,
  restartApp,
  updateLogRetention,
  updateProgram,
  type LogRetention,
  type ProgramUpdate,
} from "@/lib/api";

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

// 自重启:POST /api/config/restart(202)→ 触发后端关闭+退出 81→监督器重启。
// 前端 poll /health 两阶段重连,恢复后失效 restart-status + reload 反映新参数。
export function useRestartApp() {
  const qc = useQueryClient();
  const [restarting, setRestarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const mutate = useMutation({
    mutationFn: restartApp,
    onSuccess: async () => {
      setRestarting(true);
      setError(null);
      try {
        await awaitReconnect(60_000);
        qc.invalidateQueries({ queryKey: ["restart-status"] });
        window.location.reload();
      } catch {
        setError("重启超时,请手动检查后刷新页面。");
        setRestarting(false);
      }
    },
    onError: (e: unknown) => setError((e as Error).message),
  });
  return { triggerRestart: () => mutate.mutate(), restarting, error };
}
