// 数据管理 — storage stats / orphaned / delete (gateway/api/data_api.py)。
import { apiJson } from "./shared";

export interface ModelDataStats {
  request_count: number;
  has_runtime_data: boolean;
}
export interface StorageStats {
  size_bytes: number | null;
  total_requests: number;
  total_models_with_data: number;
  log_sessions: number;
  log_lines: number;
  models_data: Record<string, ModelDataStats>;
}
export interface OrphanedModelsResponse {
  orphaned_models: string[];
  count: number;
}

export async function fetchStorageStats(): Promise<StorageStats> {
  return apiJson<StorageStats>("/api/data/storage-stats");
}

export async function fetchOrphanedModels(): Promise<OrphanedModelsResponse> {
  return apiJson<OrphanedModelsResponse>("/api/data/models/orphaned");
}

export async function deleteModelData(name: string): Promise<{ deleted: string }> {
  return apiJson<{ deleted: string }>(`/api/data/models/${encodeURIComponent(name)}`, {
    method: "DELETE",
  });
}
