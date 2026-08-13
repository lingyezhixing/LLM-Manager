// 工具 — WOL + Claude 预设的 HTTP 表面(/api/tools/*)。
// WolConfig / ConfigWriteResult 类型留在 ./config(ConfigResponse 引用 wol;写回形状同款),此处仅函数。
// 消费方仍可经 @/lib/api barrel 取。
import { parseApiError } from "./shared";
import type { ConfigWriteResult, WolConfig } from "./config";

// WOL 配置写回(PUT /api/tools/wol,两字段必填,Pydantic 422 拦部分更新)。
export async function updateWol(body: WolConfig): Promise<ConfigWriteResult> {
  const res = await fetch("/api/tools/wol", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw await parseApiError(res);
  return (await res.json()) as ConfigWriteResult;
}

// 清除 WOL 配置(DELETE /api/tools/wol:删双键 → wol=null)。
export async function deleteWol(): Promise<ConfigWriteResult> {
  const res = await fetch("/api/tools/wol", { method: "DELETE" });
  if (!res.ok) throw await parseApiError(res);
  return (await res.json()) as ConfigWriteResult;
}

// 立即发送魔术包(POST /api/tools/wol/send;按传入地址直接发,无需先保存)。
export async function sendWol(body: WolConfig): Promise<{ ok: boolean }> {
  const res = await fetch("/api/tools/wol/send", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw await parseApiError(res);
  return (await res.json()) as { ok: boolean };
}

// Claude 预设:整组全量替换(PUT /api/tools/claude)。
export async function updateClaudeConfigs(
  configs: Record<string, Record<string, string>>,
): Promise<ConfigWriteResult> {
  const res = await fetch("/api/tools/claude", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ configs }),
  });
  if (!res.ok) throw await parseApiError(res);
  return (await res.json()) as ConfigWriteResult;
}

// 应用预设到 Claude settings.json(POST /api/tools/claude/apply)。
export async function applyClaudePreset(name: string): Promise<{ applied: string }> {
  const res = await fetch("/api/tools/claude/apply", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
  if (!res.ok) throw await parseApiError(res);
  return (await res.json()) as { applied: string };
}

// 当前生效预设(GET /api/tools/claude/current,探测不到 "(未知)")。
export async function fetchClaudeCurrent(): Promise<{ current: string }> {
  const res = await fetch("/api/tools/claude/current");
  if (!res.ok) throw await parseApiError(res);
  return (await res.json()) as { current: string };
}
