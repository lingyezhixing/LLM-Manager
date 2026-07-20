// 概览 API types + fetchers. Hand-defined here to match the backend Pydantic response
// models (gateway/api/{models,devices,usage}.py) — the frontend's single source of truth.
export interface ModelInfo {
  alias: string;
  mode: string;
  port: number;
  auto_start: boolean;
  status: string;
  pid: number | null;
  pending: number;
  failure_reason: string | null;
  started_at: number | null;   // wall-clock epoch when entered ROUTING (null if not routing)
  last_access: number;         // wall-clock epoch of last activity (0 if never)
}
export interface ModelsResponse { data: ModelInfo[]; }

export async function fetchModels(): Promise<ModelsResponse> {
  const res = await fetch("/api/models");
  if (!res.ok) throw new Error(`/api/models failed: ${res.status}`);
  return (await res.json()) as ModelsResponse;
}

// Device + session types hand-defined (match gateway/api/devices.py + usage.py).
export interface DeviceInfo {
  device_name: string;
  device_type: string;
  memory_type: string;
  total_memory_mb: number;
  available_memory_mb: number;
  used_memory_mb: number;
  usage_percentage: number;
  temperature_celsius: number | null;
}
export interface DevicesResponse { data: DeviceInfo[]; }

export interface SessionUsage {
  started_at: number;       // process start (wall-clock epoch seconds) — frontend ticks uptime
  input_tokens: number;
  output_tokens: number;
  cache_hit: number;
  cache_miss: number;
  hit_rate: number;
}

export async function fetchSessionUsage(): Promise<SessionUsage> {
  const res = await fetch("/api/usage/session");
  if (!res.ok) throw new Error(`/api/usage/session failed: ${res.status}`);
  return (await res.json()) as SessionUsage;
}

export interface HealthResponse {
  status: string;
}

export async function fetchHealth(): Promise<HealthResponse> {
  const res = await fetch("/health");
  if (!res.ok) throw new Error(`/health failed: ${res.status}`);
  return (await res.json()) as HealthResponse;
}

export interface UsageSeries {
  buckets: number[];                       // bucket-start wall-clock epochs (chart x-axis)
  total: number[];                         // tokens per bucket, summed across models
  models: Record<string, number[]>;        // model name → tokens per bucket
}

export type UsageSeriesParams = { range: string } | { start: number; end: number };

export async function fetchUsageSeries(params: UsageSeriesParams): Promise<UsageSeries> {
  const qs = new URLSearchParams(
    "range" in params ? { range: params.range } : { start: String(params.start), end: String(params.end) },
  );
  const res = await fetch(`/api/usage/series?${qs.toString()}`);
  if (!res.ok) throw new Error(`/api/usage/series failed: ${res.status}`);
  return (await res.json()) as UsageSeries;
}

export interface UsageSummary {
  input_tokens: number;
  output_tokens: number;
  cache_hit: number;
  cache_miss: number;
  hit_rate: number;
  request_count: number;
}

export async function fetchUsageSummary(params: UsageSeriesParams): Promise<UsageSummary> {
  const qs = new URLSearchParams(
    "range" in params ? { range: params.range } : { start: String(params.start), end: String(params.end) },
  );
  const res = await fetch(`/api/usage/summary?${qs.toString()}`);
  if (!res.ok) throw new Error(`/api/usage/summary failed: ${res.status}`);
  return (await res.json()) as UsageSummary;
}

export interface ByModelEntry {
  model: string;
  input_tokens: number;
  output_tokens: number;
  cache_n: number;
  request_count: number;
  hit_rate: number;
  share: number;
  latency_ms: number;
}

export async function fetchUsageByModel(params: UsageSeriesParams): Promise<ByModelEntry[]> {
  const qs = new URLSearchParams(
    "range" in params ? { range: params.range } : { start: String(params.start), end: String(params.end) },
  );
  const res = await fetch(`/api/usage/by-model?${qs.toString()}`);
  if (!res.ok) throw new Error(`/api/usage/by-model failed: ${res.status}`);
  return (await res.json()) as ByModelEntry[];
}

// 模型管理 — per-model control + structured log stream. LogLine matches the SSE frame the
// backend emits on /api/models/{alias}/logs/stream (LogLineResponse in gateway/api/models.py;
// captured/leveled in data/logs.py).
export interface LogLine {
  id: number; ts: number; stream: "out" | "err";
  level: "info" | "ok" | "warn" | "error"; text: string;
}

export async function startModel(alias: string): Promise<void> {
  const res = await fetch(`/api/models/${encodeURIComponent(alias)}/start`, { method: "POST" });
  if (!res.ok && res.status !== 409) throw new Error(`/start failed: ${res.status}`);
}
export async function stopModel(alias: string): Promise<void> {
  const res = await fetch(`/api/models/${encodeURIComponent(alias)}/stop`, { method: "POST" });
  if (!res.ok) throw new Error(`/stop failed: ${res.status}`);
}

// 日志搜索 / 翻页(本次会话全量在后端,前端按需取一页)。
export interface LogSearch { matches: number[]; total: number; }

export async function fetchLogPage(
  alias: string, before: number, limit = 1500, level?: string,
): Promise<LogLine[]> {
  const qs = new URLSearchParams({ before: String(before), limit: String(limit) });
  if (level) qs.set("level", level);
  const res = await fetch(`/api/models/${encodeURIComponent(alias)}/logs?${qs}`);
  if (!res.ok) throw new Error(`/logs failed: ${res.status}`);
  return (await res.json()) as LogLine[];
}

export async function searchLogs(alias: string, q: string, level?: string): Promise<LogSearch> {
  const qs = new URLSearchParams({ q });
  if (level) qs.set("level", level);
  const res = await fetch(`/api/models/${encodeURIComponent(alias)}/logs/search?${qs}`);
  if (!res.ok) throw new Error(`/logs/search failed: ${res.status}`);
  return (await res.json()) as LogSearch;
}

// 系统配置 — config 写回 + system info. Types hand-defined to match
// gateway/api/config_api.py (ProgramUpdate / GET /api/config / GET /api/system/info).
export interface SystemInfo {
  version: string;
  started_at: number;        // epoch seconds (time.time())
  uptime_s: number;
  db_path: string;
  db_size_bytes: number | null;
  log_dir: string;
}

export interface ProgramConfig {
  host: string;
  port: number;
  alive_time: number;
  log_level: string;
  log_dir: string;
  db_path: string;
  claude_settings_path: string;
}

export interface ConfigResponse {
  program: ProgramConfig;
  wol: { broadcast_address: string; mac_address: string } | null;
  claude: Record<string, Record<string, string>>;
  logs: { time_enabled: boolean; days: number; count_enabled: boolean; count: number };
  restart_fields: string[];
}

export interface ConfigWriteResult {
  needs_restart: boolean;
  restart_fields: string[];
  serving: string[];
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

export async function updateProgram(body: ProgramUpdate): Promise<ConfigWriteResult> {
  const res = await fetch("/api/config/program", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`/api/config/program failed: ${res.status}`);
  return (await res.json()) as ConfigWriteResult;
}
