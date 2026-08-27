import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createProvider,
  deleteProvider,
  fetchProvider,
  fetchProviders,
  updateProvider,
  type ProviderDef,
} from "@/lib/api";
import { qk } from "@/lib/api/keys";

// 查询键独立于 ["config"](配置更新不连带重取服务商列表);mutation 内联失效。
export function useProviders() {
  return useQuery({ queryKey: qk.providerDefs, queryFn: fetchProviders });
}

// name=null 时 enabled:false(创建态不取详情)。
export function useProvider(name: string | null) {
  return useQuery({
    queryKey: name !== null ? qk.providerDef(name) : ["provider-defs", null] as const,
    queryFn: () => fetchProvider(name as string),
    enabled: name !== null,
  });
}

export function useCreateProvider() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ProviderDef) => createProvider(body),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.providerDefs }),
  });
}

// mutationFn 接收 {body, migrate}:migrate 仅改名时经表单确认后传入。
// 前缀失效 list(含 detail);表单用 baseline,refetch 不打断编辑。
export function useUpdateProvider(name: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars: { body: ProviderDef; migrate: boolean }) =>
      updateProvider(name, vars.body, vars.migrate),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.providerDefs });
    },
  });
}

export function useDeleteProvider() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => deleteProvider(name),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.providerDefs }),
  });
}
