// 模型/设备类型 + 模型控制 + 模型定义 CRUD。Types hand-defined to match
// gateway/api/{models,devices}.py + config_api.py (ModelDefInput / GET /api/config/models[/{name}])。
import { parseApiError } from "./shared";

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

// Device types hand-defined (match gateway/api/devices.py)。
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

// 模型控制:start/stop/restart。restart = stop→ensure_running(读穿取新配置)。202 异步;
// 运行态经 SSE 反映,无需失效查询键。错误统一走 parseApiError(F7);start 对 409(已运行)
// 幂等放行——启动一个已在运行的模型对用户不是错误。
export async function startModel(alias: string): Promise<void> {
  const res = await fetch(`/api/models/${encodeURIComponent(alias)}/start`, { method: "POST" });
  if (res.status === 409) return;
  if (!res.ok) throw await parseApiError(res);
}
export async function stopModel(alias: string): Promise<void> {
  const res = await fetch(`/api/models/${encodeURIComponent(alias)}/stop`, { method: "POST" });
  if (!res.ok) throw await parseApiError(res);
}
export async function restartModel(alias: string): Promise<void> {
  const res = await fetch(`/api/models/${encodeURIComponent(alias)}/restart`, { method: "POST" });
  if (!res.ok) throw await parseApiError(res);
}

// 模型定义 CRUD — types + fetchers. Match gateway/api/config_api.py
// (ModelDefInput / GET /api/config/models[/{name}])。
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

// migrate_data:仅改名(body.name≠路径 name)时生效——true=把历史用量/成本/日志迁到新名。
export async function updateModelDef(
  name: string,
  body: ModelDef,
  migrate_data = false,
): Promise<ModelWriteResult> {
  const res = await fetch(
    `/api/config/models/${encodeURIComponent(name)}?migrate_data=${migrate_data}`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    },
  );
  if (!res.ok) throw await parseApiError(res);
  return (await res.json()) as ModelWriteResult;
}

export async function deleteModelDef(name: string): Promise<void> {
  const res = await fetch(`/api/config/models/${encodeURIComponent(name)}`, { method: "DELETE" });
  if (!res.ok) throw await parseApiError(res);
}
