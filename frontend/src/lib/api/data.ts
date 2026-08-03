// 数据管理 — storage stats / orphaned / delete (gateway/api/data_api.py)。
import { parseApiError } from "./shared";

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
  const res = await fetch("/api/data/storage-stats");
  if (!res.ok) throw await parseApiError(res);
  return (await res.json()) as StorageStats;
}

export async function fetchOrphanedModels(): Promise<OrphanedModelsResponse> {
  const res = await fetch("/api/data/models/orphaned");
  if (!res.ok) throw await parseApiError(res);
  return (await res.json()) as OrphanedModelsResponse;
}

export async function deleteModelData(name: string): Promise<{ deleted: string }> {
  const res = await fetch(`/api/data/models/${encodeURIComponent(name)}`, { method: "DELETE" });
  if (!res.ok) throw await parseApiError(res);
  return (await res.json()) as { deleted: string };
}
