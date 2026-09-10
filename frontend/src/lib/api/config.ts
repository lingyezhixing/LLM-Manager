// 系统配置 — config 写回 + 系统信息。类型手写定义,对齐
// gateway/api/config_api.py (ProgramUpdate / GET /api/config / GET /api/system/info)。
import { apiJson } from "./shared";

export interface SystemInfo {
  version: string;
  started_at: number;        // epoch 秒(time.time())
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
  /** 当前运行实例的 program(启动期捕获):「保存前预检」与「恢复运行值」的依据。 */
  running_program: ProgramConfig;
  wol: WolConfig | null;
  claude: Record<string, Record<string, string>>;
  logs: LogRetention;
  restart_fields: string[];
}

export interface ConfigWriteResult {
  needs_restart: boolean;
  restart_fields: string[];
  serving: string[];
  /** 仅 /api/tools/claude 带 apply 时返回:已同步应用的预设名。 */
  applied?: string;
}

export interface WolConfig {
  broadcast_address: string;
  mac_address: string;
}

// 日志保留规则写回(PUT /api/config/logs;日志规则已并入 AppConfig 快照,恒不触发重启)。
export async function updateLogRetention(body: LogRetention): Promise<ConfigWriteResult> {
  return apiJson<ConfigWriteResult>("/api/config/logs", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
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
  await apiJson<void>("/api/config/restart", { method: "POST" });
}

// dryRun=true:只做冲突检测(restart_fields),不落库——「预检→确认→落库」流专用。
export async function updateProgram(body: ProgramUpdate, dryRun = false): Promise<ConfigWriteResult> {
  return apiJson<ConfigWriteResult>(
    `/api/config/program${dryRun ? "?dry_run=true" : ""}`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    },
  );
}
