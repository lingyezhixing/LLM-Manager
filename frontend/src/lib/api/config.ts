// 系统配置 — config 写回 + system info. Types hand-defined to match
// gateway/api/config_api.py (ProgramUpdate / GET /api/config / GET /api/system/info)。
import { parseApiError } from "./shared";

export interface SystemInfo {
  version: string;
  started_at: number;        // epoch seconds (time.time())
  uptime_s: number;
  db_size_bytes: number | null;
}

export interface ProgramConfig {
  host: string;
  port: number;
  alive_time: number;
  log_level: string;
  claude_settings_path: string;
}

// 日志保留规则(GET/PUT /api/config/logs;恒生效:按时间保留 N 天 + 按条数保留 N 条,系统与模型日志同时适用)。
export interface LogRetention {
  days: number;
  count: number;
}

export interface ConfigResponse {
  program: ProgramConfig;
  wol: { broadcast_address: string; mac_address: string } | null;
  claude: Record<string, Record<string, string>>;
  logs: LogRetention;
  restart_fields: string[];
}

export interface ConfigWriteResult {
  needs_restart: boolean;
  restart_fields: string[];
  serving: string[];
}

export interface WolConfig {
  broadcast_address: string;
  mac_address: string;
}

// 系统页网络区:WOL 配置写回(PUT /api/config/wol,两字段必填,Pydantic 422 拦部分更新)。
export async function updateWol(body: WolConfig): Promise<ConfigWriteResult> {
  const res = await fetch("/api/config/wol", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw await parseApiError(res);
  return (await res.json()) as ConfigWriteResult;
}

// 清除 WOL 配置(DELETE /api/config/wol:删双键 → wol=null,托盘动作提示未配置)。
export async function deleteWol(): Promise<ConfigWriteResult> {
  const res = await fetch("/api/config/wol", { method: "DELETE" });
  if (!res.ok) throw await parseApiError(res);
  return (await res.json()) as ConfigWriteResult;
}

// Claude 预设:整组全量替换(PUT /api/config/claude)。
export async function updateClaudeConfigs(configs: Record<string, Record<string, string>>): Promise<ConfigWriteResult> {
  const res = await fetch("/api/config/claude", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ configs }),
  });
  if (!res.ok) throw await parseApiError(res);
  return (await res.json()) as ConfigWriteResult;
}

// 日志保留规则写回(PUT /api/config/logs;日志规则已并入 AppConfig 快照,恒不触发重启)。
export async function updateLogRetention(body: LogRetention): Promise<ConfigWriteResult> {
  const res = await fetch("/api/config/logs", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw await parseApiError(res);
  return (await res.json()) as ConfigWriteResult;
}

// 应用预设到 Claude settings.json(POST /api/config/claude/apply)。
export async function applyClaudePreset(name: string): Promise<{ applied: string }> {
  const res = await fetch("/api/config/claude/apply", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
  if (!res.ok) throw await parseApiError(res);
  return (await res.json()) as { applied: string };
}

// 当前生效预设(GET /api/config/claude/current,探测不到 "(未知)")。
export async function fetchClaudeCurrent(): Promise<{ current: string }> {
  const res = await fetch("/api/config/claude/current");
  if (!res.ok) throw await parseApiError(res);
  return (await res.json()) as { current: string };
}

export type ProgramUpdate = Partial<ProgramConfig>;

export async function fetchSystemInfo(): Promise<SystemInfo> {
  const res = await fetch("/api/system/info");
  if (!res.ok) throw new Error(`/api/system/info failed: ${res.status}`);
  return (await res.json()) as SystemInfo;
}

export async function fetchConfig(): Promise<ConfigResponse> {
  const res = await fetch("/api/config");
  if (!res.ok) throw new Error(`/api/config failed: ${res.status}`);
  return (await res.json()) as ConfigResponse;
}

export async function fetchRestartStatus(): Promise<ConfigWriteResult> {
  const res = await fetch("/api/config/restart-status");
  if (!res.ok) throw new Error(`/api/config/restart-status failed: ${res.status}`);
  return (await res.json()) as ConfigWriteResult;
}

// 自重启:后端优雅关闭 + sys.exit(81)→ 监督器重启。202;失败抛 parseApiError。
export async function restartApp(): Promise<void> {
  const res = await fetch("/api/config/restart", { method: "POST" });
  if (!res.ok) throw await parseApiError(res);
}

export async function updateProgram(body: ProgramUpdate): Promise<ConfigWriteResult> {
  const res = await fetch("/api/config/program", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw await parseApiError(res);
  return (await res.json()) as ConfigWriteResult;
}
