import { apiJson } from "./shared";

// 用量聚合 + 计费成本。Types hand-defined to match gateway/api/usage.py 的响应模型
// (UsageSummaryResponse / UsageSeriesResponse / CostSummaryResponse)。
export interface SessionUsage {
  started_at: number;       // process start (wall-clock epoch seconds) — frontend ticks uptime
  input_tokens: number;
  output_tokens: number;
  cache_hit: number;
  cache_miss: number;
  hit_rate: number;
  total_cost: number;       // 本次启动消耗金额(后端 compute-on-read 窗口 [started_at, now))
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
  buckets: number[];                       // bucket-start wall-clock epochs (chart x-axis)
  total: number[];                         // tokens per bucket, summed across models
  models: Record<string, number[]>;        // model name → tokens per bucket
}

export type UsageSeriesParams = { period: string } | { start: number; end: number };

// period 快捷窗口 ↔ 显式 [start,end] 两种参数形态 → URL 查询串。
function qsForParams(params: UsageSeriesParams): string {
  const q: Record<string, string> = "period" in params
    ? { period: params.period }
    : { start: String(params.start), end: String(params.end) };
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
}

export async function fetchUsageByModel(params: UsageSeriesParams): Promise<ByModelEntry[]> {
  return apiJson<ByModelEntry[]>(`/api/usage/by-model?${qsForParams(params)}`);
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
  return apiJson<CostSummary>(`/api/usage/cost?${qsForParams(params)}`);
}

export async function fetchUsageCostSeries(params: UsageSeriesParams): Promise<UsageSeries> {
  return apiJson<UsageSeries>(`/api/usage/cost-series?${qsForParams(params)}`);
}
