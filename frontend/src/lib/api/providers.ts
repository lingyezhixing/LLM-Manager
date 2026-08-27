// 云服务商 CRUD — 类型 + 取数函数,对齐 gateway/api/config_api.py
// (ProviderInput / GET /api/config/providers[/{name}])。
// 读(GET 详情)与写(ProviderInput)同形,前端用单一 ProviderDef 表达两者。
import { apiJson } from "./shared";
import type { ModelWriteResult } from "./models";

// 云端计费阶梯 — 与本地 PricingTier 同构(对齐 CloudTierInput / PricingTier.to_dict())。
export interface CloudTier {
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

// 峰谷时段:start_min/end_min 为当天分钟数(0-1439),start > end 表示跨午夜窗口。
export interface CloudTimeWindow {
  start_min: number;
  end_min: number;
}

export interface CloudModel {
  model_name: string;
  support_cache: boolean;   // 是否支持 prompt 缓存(缓存计费开关)
  dual_pricing: boolean;    // 峰谷双价(峰=base,谷=offpeak)
  offpeak_windows: CloudTimeWindow[];
  tiers_base: CloudTier[];         // 峰/常价阶梯
  tiers_offpeak: CloudTier[];      // 谷价阶梯(dual_pricing=false 时忽略)
}

export interface CloudMapping {
  local_path: string;       // 本地请求路径(全局唯一,精确匹配)
  target_url: string;       // 完整云端 URL(含协议,可带 query)
  auth_style: "bearer" | "x-api-key" | "none";
}

export interface ProviderDef {
  name: string;
  api_key: string;
  enabled: boolean;
  openai_base: string;      // OpenAI 传统 API 族 base(留空 = 该接口族不支持)
  responses_base: string;   // Responses API 族 base
  claude_base: string;      // Anthropic API 族 base
  extra_headers: Record<string, string>;
  models: CloudModel[];
  mappings: CloudMapping[];
}

// 列表端点摘要:无 models/mappings 明细,仅计数。
export interface ProviderSummary {
  name: string;
  enabled: boolean;
  openai_base: string;
  responses_base: string;
  claude_base: string;
  model_count: number;
  mapping_count: number;
}

export async function fetchProviders(): Promise<ProviderSummary[]> {
  return apiJson<ProviderSummary[]>("/api/config/providers");
}

export async function fetchProvider(name: string): Promise<ProviderDef> {
  return apiJson<ProviderDef>(`/api/config/providers/${encodeURIComponent(name)}`);
}

export async function createProvider(body: ProviderDef): Promise<void> {
  await apiJson<void>("/api/config/providers", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

// migrate_data:仅改名(body.name≠路径 name)时生效——true=把历史用量/成本/日志迁到新名。
// dryRun=true:只做校验,不落库(「预检→确认→落库」流专用)。返回与模型 PUT 同形。
export async function updateProvider(
  name: string,
  body: ProviderDef,
  migrate_data = false,
  dryRun = false,
): Promise<ModelWriteResult> {
  const params = new URLSearchParams({ migrate_data: String(migrate_data) });
  if (dryRun) params.set("dry_run", "true");
  return apiJson<ModelWriteResult>(`/api/config/providers/${encodeURIComponent(name)}?${params}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function deleteProvider(name: string): Promise<void> {
  await apiJson<void>(`/api/config/providers/${encodeURIComponent(name)}`, { method: "DELETE" });
}
