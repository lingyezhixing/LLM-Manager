// 自更新 — git 标签版本身份 + 严格 ff-only。Types hand-defined to match
// gateway/api/update_api.py (UpdateStatus)。
import { apiJson } from "./shared";

export interface UpdateStatus {
  ok: boolean;
  error: string | null;
  current_version: string;
  current_sha: string;
  latest_version: string | null;
  latest_sha: string | null;
  up_to_date: boolean;
  available: boolean;
  dirty: boolean;
  conflicted: boolean;
  commits_behind: number;
}

export async function fetchUpdateStatus(): Promise<UpdateStatus> {
  return apiJson<UpdateStatus>("/api/update/status");
}

// 拉取最新代码并重启(202)。失败(dirty/分叉/网络)→ 409,detail 即原因。
export async function applyUpdate(): Promise<{ updated: boolean; sha: string }> {
  return apiJson("/api/update/apply", { method: "POST" });
}
