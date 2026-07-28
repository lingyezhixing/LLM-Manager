import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  fetchConfig,
  fetchHealth,
  fetchRestartStatus,
  fetchSystemInfo,
  restartApp,
  updateProgram,
  type ProgramUpdate,
} from "./api";

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
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["config"] });
      qc.invalidateQueries({ queryKey: ["restart-status"] });
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
