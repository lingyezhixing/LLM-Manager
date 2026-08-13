// 系统配置 — config 写回 + system info. Types hand-defined to match
// gateway/api/config_api.py (ProgramUpdate / GET /api/config / GET /api/system/info)。
import { apiJson, parseApiError } from "./shared";

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
  wol: WolConfig | null;
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

export type ProgramUpdate = Partial<ProgramConfig>;

export async function fetchSystemInfo(): Promise<SystemInfo> {
  return apiJson<SystemInfo>("/api/system/info");
}

export async function fetchConfig(): Promise<ConfigResponse> {
  return apiJson<ConfigResponse>("/api/config");
}

export async function fetchRestartStatus(): Promise<ConfigWriteResult> {
  return apiJson<ConfigWriteResult>("/api/config/restart-status");
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
