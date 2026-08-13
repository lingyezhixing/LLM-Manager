// 自更新 — git 标签版本身份 + 严格 ff-only(仅向前,无回退)。Types hand-defined
// to match gateway/api/update_api.py (UpdateStatus / UpdateTarget)。
import { apiJson } from "./shared";

export type UpdateTarget = "commit" | "tag";

export interface UpdateStatus {
  ok: boolean;
  error: string | null;
  current_version: string;
  current_sha: string;
  dirty: boolean;
  conflicted: boolean;
  tag: string | null;
  tag_sha: string | null;
  tag_available: boolean;
  commit_sha: string | null;
  commit_available: boolean;
}

export async function fetchUpdateStatus(): Promise<UpdateStatus> {
  return apiJson<UpdateStatus>("/api/update/status");
}

// 拉取所选目标并重启(202)。失败(冲突/分叉/网络/目标不可用)→ 409,detail 即原因。
export async function applyUpdate(target: UpdateTarget): Promise<{ updated: boolean; target: UpdateTarget; sha: string }> {
  return apiJson("/api/update/apply", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ target }),
  });
}
