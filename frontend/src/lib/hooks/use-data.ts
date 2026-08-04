import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { deleteModelData, fetchOrphanedModels, fetchStorageStats } from "@/lib/api";

// 数据管理页:每载入必获取(refetchOnMount: "always"),不轮询。
export function useStorageStats() {
  return useQuery({ queryKey: ["data", "storage-stats"], queryFn: fetchStorageStats, refetchOnMount: "always" });
}

export function useOrphanedModels() {
  return useQuery({ queryKey: ["data", "orphaned"], queryFn: fetchOrphanedModels, refetchOnMount: "always" });
}

// 删除孤立模型数据:成功 → 失效两查询(表格与孤立区同步刷新)。
export function useDeleteModelData() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => deleteModelData(name),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["data", "storage-stats"] });
      qc.invalidateQueries({ queryKey: ["data", "orphaned"] });
    },
  });
}
