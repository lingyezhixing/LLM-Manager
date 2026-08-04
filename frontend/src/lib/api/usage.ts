// 用量聚合 + 计费成本。Types hand-defined to match gateway/api/usage.py 的响应模型
// (UsageSummaryResponse / UsageSeriesResponse / CostSummaryResponse)。
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

export type UsageSeriesParams = { period: string } | { start: number; end: number };

export async function fetchUsageSeries(params: UsageSeriesParams): Promise<UsageSeries> {
  const qs = new URLSearchParams(
    "period" in params ? { period: params.period } : { start: String(params.start), end: String(params.end) },
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
    "period" in params ? { period: params.period } : { start: String(params.start), end: String(params.end) },
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
    "period" in params ? { period: params.period } : { start: String(params.start), end: String(params.end) },
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
    "period" in params ? { period: params.period } : { start: String(params.start), end: String(params.end) },
  );
  const res = await fetch(`/api/usage/cost?${qs.toString()}`);
  if (!res.ok) throw new Error(`/api/usage/cost failed: ${res.status}`);
  return (await res.json()) as CostSummary;
}

export async function fetchUsageCostSeries(params: UsageSeriesParams): Promise<UsageSeries> {
  const qs = new URLSearchParams(
    "period" in params ? { period: params.period } : { start: String(params.start), end: String(params.end) },
  );
  const res = await fetch(`/api/usage/cost-series?${qs.toString()}`);
  if (!res.ok) throw new Error(`/api/usage/cost-series failed: ${res.status}`);
  return (await res.json()) as UsageSeries;
}
