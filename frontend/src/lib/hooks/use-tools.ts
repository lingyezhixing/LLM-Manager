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
import { qk } from "@/lib/api/keys";

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
    mutationFn: (body: { configs: Record<string, Record<string, string>>; apply?: string }) =>
      updateClaudeConfigs(body.configs, body.apply),
    onSuccess: (data) => {
      invalidateConfig(qc);
      // 保存并生效 → settings.json 已变,current 探测须刷新,否则「生效中」标记陈旧。
      if (data.applied) qc.invalidateQueries({ queryKey: qk.claudeCurrent });
    },
  });
}

// 应用后失效 current 查询(否则「当前生效」保持陈旧)。
export function useApplyClaudePreset() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => applyClaudePreset(name),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.claudeCurrent }),
  });
}

export function useClaudeCurrent() {
  return useQuery({ queryKey: qk.claudeCurrent, queryFn: fetchClaudeCurrent });
}
