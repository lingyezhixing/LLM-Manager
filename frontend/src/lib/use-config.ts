import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  fetchConfig,
  fetchRestartStatus,
  fetchSystemInfo,
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
