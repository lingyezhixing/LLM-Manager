import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createModelDef,
  deleteModelDef,
  fetchModelDef,
  fetchModelDefs,
  restartModel,
  updateModelDef,
  type ModelDef,
} from "./api";

export function useModelDefs() {
  return useQuery({ queryKey: ["config", "models"], queryFn: fetchModelDefs });
}

// name=null 时 enabled:false(创建态不取详情)。
export function useModelDef(name: string | null) {
  return useQuery({
    queryKey: ["config", "models", name],
    queryFn: () => fetchModelDef(name as string),
    enabled: name !== null,
  });
}

export function useCreateModelDef() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ModelDef) => createModelDef(body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["config", "models"] }),
  });
}

// 失效 list(选择带摘要刷新)+ detail(同模型再挂载时缓存新鲜);表单用 baseline,refetch 不打断编辑。
export function useUpdateModelDef(name: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ModelDef) => updateModelDef(name, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["config", "models"] });
      qc.invalidateQueries({ queryKey: ["config", "models", name] });
    },
  });
}

export function useDeleteModelDef() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => deleteModelDef(name),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["config", "models"] }),
  });
}

// 运行态经 /api/models/stream SSE 推送(模型管理页),无查询键可失效;onSuccess 仅由调用方隐藏提示。
export function useRestartModel() {
  return useMutation({ mutationFn: (alias: string) => restartModel(alias) });
}
