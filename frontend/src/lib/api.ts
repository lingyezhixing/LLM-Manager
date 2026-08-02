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

// 计费成本 — cost 汇总 + cost 时间序列(序列与 usage/series 同形)。Match gateway/api/usage.py
// 的 CostSummaryResponse / UsageSeriesResponse。
export interface CostByModel {
  model: string;
  pricing_type: "tier" | "hourly";
  cost: number;
}
export interface CostSummary {
  total_cost: number;
  by_model: CostByModel[];
}

export async function fetchUsageCost(params: UsageSeriesParams): Promise<CostSummary> {
  const qs = new URLSearchParams(
    "range" in params ? { range: params.range } : { start: String(params.start), end: String(params.end) },
  );
  const res = await fetch(`/api/usage/cost?${qs.toString()}`);
  if (!res.ok) throw new Error(`/api/usage/cost failed: ${res.status}`);
  return (await res.json()) as CostSummary;
}

export async function fetchUsageCostSeries(params: UsageSeriesParams): Promise<UsageSeries> {
  const qs = new URLSearchParams(
    "range" in params ? { range: params.range } : { start: String(params.start), end: String(params.end) },
  );
  const res = await fetch(`/api/usage/cost-series?${qs.toString()}`);
  if (!res.ok) throw new Error(`/api/usage/cost-series failed: ${res.status}`);
  return (await res.json()) as UsageSeries;
}

// 模型管理 — per-model control + structured log stream. LogLine matches the SSE frame the
// backend emits on /api/models/{alias}/logs/stream (LogLineResponse in gateway/api/models.py;
// captured/leveled in data/logs.py).
export interface LogLine {
  id: number; ts: number; stream: "out" | "err" | "sys";
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

// 日志查看页 — persistent session logs (/api/logs/*)
export interface LogSession {
  id: number;
  type: "system" | "model";
  model_name: string | null;
  alias: string | null;
  start_time: number;
  end_time: number | null;
  status: "running" | "ended";
  duration_s: number | null;
  line_count: number;
}

export async function fetchSessions(
  opts: { type?: "system" | "model"; model?: string; limit?: number; before?: number } = {},
): Promise<LogSession[]> {
  const qs = new URLSearchParams();
  if (opts.type) qs.set("type", opts.type);
  if (opts.model) qs.set("model", opts.model);
  if (opts.limit) qs.set("limit", String(opts.limit));
  if (opts.before) qs.set("before", String(opts.before));
  const res = await fetch(`/api/logs/sessions?${qs}`);
  if (!res.ok) throw new Error(`/api/logs/sessions failed: ${res.status}`);
  return (await res.json()) as LogSession[];
}

export async function fetchSessionLines(
  sessionId: number, before: number, limit = 1500, level?: string,
): Promise<LogLine[]> {
  const qs = new URLSearchParams({ before: String(before), limit: String(limit) });
  if (level) qs.set("level", level);
  const res = await fetch(`/api/logs/sessions/${sessionId}/lines?${qs}`);
  if (!res.ok) throw new Error(`/api/logs/sessions/${sessionId}/lines failed: ${res.status}`);
  return (await res.json()) as LogLine[];
}

export async function searchSessionLogs(
  sessionId: number, q: string, level?: string,
): Promise<LogSearch> {
  const qs = new URLSearchParams({ q, session_id: String(sessionId) });
  if (level) qs.set("level", level);
  const res = await fetch(`/api/logs/search?${qs}`);
  if (!res.ok) throw new Error(`/api/logs/search failed: ${res.status}`);
  return (await res.json()) as LogSearch;
}

// 系统配置 — config 写回 + system info. Types hand-defined to match
// gateway/api/config_api.py (ProgramUpdate / GET /api/config / GET /api/system/info).
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

// 日志保留规则写回(PUT /api/config/logs;不进 AppConfig 快照,恒不触发重启)。
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

// 模型定义 CRUD — types + fetchers. Match gateway/api/config_api.py
// (ModelDefInput / GET /api/config/models[/{name}]) + models.py 的 /restart。
// 读(GET 详情)与写(ModelDefInput)同形,前端用单一 ModelDef 表达两者。
export interface CommandDef {
  exe: string;
  args: string[];
  env: Record<string, string>;
  cwd: string | null;
  conda_env: string | null;
}
export interface SchemeDef {
  config_source: string;
  required_devices: string[];
  command: CommandDef;
  memory_mb: Record<string, number>;
}
// 计费定价 — 对应 GET /api/config/models/{name} 的 _pricing_dict 输出(逐字段同形)。
export interface PricingTier {
  tier_index: number;
  min_input: number | null;
  max_input: number | null;
  min_output: number | null;
  max_output: number | null;
  input_price: number;
  output_price: number;
  cache_write_price: number;
  cache_read_price: number;
}
export interface Pricing {
  pricing_type: "tier" | "hourly";
  hourly_price: number;
  support_cache: boolean;   // 模型级:是否支持 prompt 缓存(缓存计费开关)
  tiers: PricingTier[];
}
export interface ModelDef {
  name: string;
  mode: string;
  port: number;
  auto_start: boolean;
  aliases: string[];
  schemes: SchemeDef[];
  pricing: Pricing;
}

// 列表端点摘要:schemes 仅回 config_source 键(list(m.schemes)),非全量对象。
export interface ModelDefSummary {
  name: string;
  mode: string;
  port: number;
  auto_start: boolean;
  aliases: string[];
  schemes: string[];
}

export interface ModelWriteResult {
  affected_routing: string[];
  hint: string | null;
}

// 模型 CRUD 422 有三种 detail 形态:config.validate 的 list[str]、ValueError 的 str、
// Pydantic 字段错的 list[{loc,msg,...}];409 detail 为 str。统一解析为一句可读消息。
export async function parseApiError(res: Response): Promise<Error> {
  let msg = `请求失败: ${res.status}`;
  try {
    const body = await res.json() as { detail?: unknown };
    const d = body?.detail;
    if (Array.isArray(d)) {
      msg = d
        .map((x) => (typeof x === "string" ? x : (x as { msg?: string })?.msg ?? JSON.stringify(x)))
        .join("; ");
    } else if (typeof d === "string") {
      msg = d;
    } else if (typeof body === "string") {
      msg = body;
    }
  } catch {
    // 非 JSON 响应:保留 status
  }
  return new Error(msg);
}

export async function fetchModelDefs(): Promise<ModelDefSummary[]> {
  const res = await fetch("/api/config/models");
  if (!res.ok) throw await parseApiError(res);
  return (await res.json()) as ModelDefSummary[];
}

export async function fetchModelDef(name: string): Promise<ModelDef> {
  const res = await fetch(`/api/config/models/${encodeURIComponent(name)}`);
  if (!res.ok) throw await parseApiError(res);
  return (await res.json()) as ModelDef;
}

export async function createModelDef(body: ModelDef): Promise<void> {
  const res = await fetch("/api/config/models", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw await parseApiError(res);
}

export async function updateModelDef(name: string, body: ModelDef): Promise<ModelWriteResult> {
  const res = await fetch(`/api/config/models/${encodeURIComponent(name)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw await parseApiError(res);
  return (await res.json()) as ModelWriteResult;
}

export async function deleteModelDef(name: string): Promise<void> {
  const res = await fetch(`/api/config/models/${encodeURIComponent(name)}`, { method: "DELETE" });
  if (!res.ok) throw await parseApiError(res);
}

// restart = stop→ensure_running(读穿取新配置)。202 异步;运行态经 SSE 反映,无需失效查询键。
export async function restartModel(alias: string): Promise<void> {
  const res = await fetch(`/api/models/${encodeURIComponent(alias)}/restart`, { method: "POST" });
  if (!res.ok) throw await parseApiError(res);
}

// 数据管理 — storage stats / orphaned / delete (gateway/api/data_api.py)
export interface ModelDataStats {
  request_count: number;
  has_runtime_data: boolean;
}
export interface StorageStats {
  size_bytes: number | null;
  total_requests: number;
  total_models_with_data: number;
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
