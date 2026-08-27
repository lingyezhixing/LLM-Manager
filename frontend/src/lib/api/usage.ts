import { apiJson } from "./shared";

// 用量聚合 + 计费成本。类型手写定义,对齐 gateway/api/usage.py 的响应模型
// (UsageSummaryResponse / UsageSeriesResponse / CostSummaryResponse)。
// source 过滤:all=不区分 / local / cloud(本地 vs 云端计费来源,由后端 ByModelEntry.source 推导)。
export type UsageSource = "all" | "local" | "cloud";

export interface SessionUsage {
  started_at: number;       // 进程启动时刻(wall-clock epoch 秒)— 前端据此跳动 uptime
  input_tokens: number;
  output_tokens: number;
  cache_hit: number;
  cache_miss: number;
  hit_rate: number;
  total_cost: number;       // 本次启动消耗金额(后端 compute-on-read 窗口 [started_at, now))
  local_cost: number;       // 本次启动本地模型部分成本
  cloud_cost: number;       // 本次启动云端模型部分成本
}

export async function fetchSessionUsage(): Promise<SessionUsage> {
  return apiJson<SessionUsage>("/api/usage/session");
}

export interface HealthResponse {
  status: string;
}

export async function fetchHealth(): Promise<HealthResponse> {
  return apiJson<HealthResponse>("/health");
}

export interface UsageSeries {
  buckets: number[];                       // 桶起始的墙钟 epoch(图表 x 轴)
  total: number[];                         // 每桶 token 数,跨模型求和
  models: Record<string, number[]>;        // 模型名 → 每桶 token 数
}

export type UsageSeriesParams =
  | { period: string; source?: UsageSource }
  | { start: number; end: number; source?: UsageSource };

// period 快捷窗口 ↔ 显式 [start,end] 两种参数形态 → URL 查询串。
// source 缺省 / "all" 不并入查询串(向后兼容;后端默认即 all)。
export function qsForParams(params: UsageSeriesParams): string {
  const q: Record<string, string> = "period" in params
    ? { period: params.period }
    : { start: String(params.start), end: String(params.end) };
  if ("source" in params && params.source && params.source !== "all") {
    q.source = params.source;
  }
  return new URLSearchParams(q).toString();
}

export async function fetchUsageSeries(params: UsageSeriesParams): Promise<UsageSeries> {
  return apiJson<UsageSeries>(`/api/usage/series?${qsForParams(params)}`);
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
  return apiJson<UsageSummary>(`/api/usage/summary?${qsForParams(params)}`);
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
  source: string;           // 行来源 "local"/"cloud"(计费来源)
}

export async function fetchUsageByModel(params: UsageSeriesParams): Promise<ByModelEntry[]> {
  return apiJson<ByModelEntry[]>(`/api/usage/by-model?${qsForParams(params)}`);
}

// 计费成本 — cost 汇总 + cost 时间序列(序列与 usage/series 同形)。对齐 gateway/api/usage.py
// 的 CostSummaryResponse / UsageSeriesResponse。
export interface CostByModel {
  model: string;
  pricing_type: "tier" | "hourly";
  cost: number;
  source: string;           // 计费来源 "local"/"cloud"
}
export interface CostSummary {
  total_cost: number;
  by_model: CostByModel[];
  local_cost: number;       // by_model 中 source=="local" 的成本和
  cloud_cost: number;       // by_model 中 source=="cloud" 的成本和
}

export async function fetchUsageCost(params: UsageSeriesParams): Promise<CostSummary> {
  return apiJson<CostSummary>(`/api/usage/cost?${qsForParams(params)}`);
}

export async function fetchUsageCostSeries(params: UsageSeriesParams): Promise<UsageSeries> {
  return apiJson<UsageSeries>(`/api/usage/cost-series?${qsForParams(params)}`);
}
