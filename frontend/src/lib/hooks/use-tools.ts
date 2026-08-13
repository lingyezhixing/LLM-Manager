import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  applyClaudePreset,
  deleteWol,
  fetchClaudeCurrent,
  sendWol,
  updateClaudeConfigs,
  updateWol,
  type WolConfig,
} from "@/lib/api";
import { invalidateConfig } from "@/lib/hooks/use-config";

export function useUpdateWol() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: WolConfig) => updateWol(body),
    onSuccess: () => invalidateConfig(qc),
  });
}

export function useDeleteWol() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => deleteWol(),
    onSuccess: () => invalidateConfig(qc),
  });
}

// 发送魔术包(无副作用于配置,不需失效)。
export function useSendWol() {
  return useMutation({
    mutationFn: (body: WolConfig) => sendWol(body),
  });
}

export function useUpdateClaudeConfigs() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (configs: Record<string, Record<string, string>>) => updateClaudeConfigs(configs),
    onSuccess: () => invalidateConfig(qc),
  });
}

// 应用后失效 current 查询(否则「当前生效」保持陈旧)。
export function useApplyClaudePreset() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => applyClaudePreset(name),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["tools", "claude", "current"] }),
  });
}

export function useClaudeCurrent() {
  return useQuery({ queryKey: ["tools", "claude", "current"], queryFn: fetchClaudeCurrent });
}
