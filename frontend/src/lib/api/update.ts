// 自更新 — git 标签版本身份 + 严格 ff-only(仅向前,无回退)。类型手写定义,
// 对齐 gateway/api/update_api.py (UpdateStatus / UpdateTarget)。
//
// 检测语义:程序启动时后端后台检测一次并缓存,前端 GET 只读缓存(刷新/进页不再
// 触发检测);手动点「检查更新」走 POST /check 触发一次全新检测。
import { apiJson } from "./shared";

export type UpdateTarget = "commit" | "tag";

export interface UpdateStatus {
  ok: boolean;
  supported: boolean;
  checking: boolean;
  error: string | null;
  current_version: string;
  current_sha: string;
  dirty: boolean;
  conflicted: boolean;
  tag: string | null;
  tag_available: boolean;
  tag_behind: number;
  commit_sha: string | null;
  commit_available: boolean;
  commit_behind: number;
}

// 读启动检测缓存(无网络;启动检测未完成 → checking=True)。
export async function fetchUpdateStatus(): Promise<UpdateStatus> {
  return apiJson<UpdateStatus>("/api/update/status");
}

// 手动检查更新(仅「检查更新」按钮触发,后端跑一次全新 git fetch 对比)。
export async function checkUpdate(): Promise<UpdateStatus> {
  return apiJson<UpdateStatus>("/api/update/check", { method: "POST" });
}

// 拉取所选目标并重启(202)。失败(冲突/分叉/网络/目标不可用)→ 409,detail 即原因。
export async function applyUpdate(target: UpdateTarget): Promise<{ updated: boolean; target: UpdateTarget; sha: string }> {
  return apiJson("/api/update/apply", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ target }),
  });
}
