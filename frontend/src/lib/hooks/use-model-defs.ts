import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createModelDef,
  deleteModelDef,
  fetchModelDef,
  fetchModelDefs,
  restartModel,
  updateModelDef,
  type ModelDef,
} from "@/lib/api";
import { qk } from "@/lib/api/keys";

// 查询键独立于 ["config"](配置更新不连带重取模型列表);mutation 内联失效。
export function useModelDefs() {
  return useQuery({ queryKey: qk.modelDefs, queryFn: fetchModelDefs });
}

// name=null 时 enabled:false(创建态不取详情)。
export function useModelDef(name: string | null) {
  return useQuery({
    queryKey: name !== null ? qk.modelDef(name) : ["model-defs", null] as const,
    queryFn: () => fetchModelDef(name as string),
    enabled: name !== null,
  });
}

export function useCreateModelDef() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ModelDef) => createModelDef(body),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.modelDefs }),
  });
}

// mutationFn 接收 {body, migrate}:migrate 仅改名时由表单经 useConfirm 询问后传入。
// 前缀失效 list(含 detail);表单用 baseline,refetch 不打断编辑。
export function useUpdateModelDef(name: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars: { body: ModelDef; migrate: boolean }) => updateModelDef(name, vars.body, vars.migrate),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.modelDefs });
    },
  });
}

export function useDeleteModelDef() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => deleteModelDef(name),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.modelDefs }),
  });
}

// 运行态经 /api/models/stream SSE 推送(模型管理页),无查询键可失效;onSuccess 仅由调用方隐藏提示。
export function useRestartModel() {
  return useMutation({ mutationFn: (alias: string) => restartModel(alias) });
}
