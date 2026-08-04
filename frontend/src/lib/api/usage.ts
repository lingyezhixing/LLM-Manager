// 用量聚合 + 计费成本。
// S2:响应类型从后端 OpenAPI 生成(schema.d.ts,`python scripts/gen_types.py` 再生成),
// 不再手写——防「改后端忘改前端」漂移。下方 fetch 函数的返回类型 = 生成的 *Response 别名。
import type {
  ByModelEntryResponse,
  CostByModelResponse,
  CostSummaryResponse,
  SessionUsageResponse,
  UsageSeriesResponse,
  UsageSummaryResponse,
} from "./schema";

export type SessionUsage = SessionUsageResponse;
export type UsageSummary = UsageSummaryResponse;
export type ByModelEntry = ByModelEntryResponse;
export type UsageSeries = UsageSeriesResponse;       // buckets/total/models:时间序列(成本序列同形)
export type CostByModel = CostByModelResponse;
export type CostSummary = CostSummaryResponse;

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

// 区间参数:预设(period)或自定义起止秒(epoch)。与后端 _resolve_range 对齐。
export type UsageSeriesParams = { period: string } | { start: number; end: number };

function qsFor(params: UsageSeriesParams): URLSearchParams {
  return new URLSearchParams(
    "period" in params
      ? { period: params.period }
      : { start: String(params.start), end: String(params.end) },
  );
}

export async function fetchUsageSeries(params: UsageSeriesParams): Promise<UsageSeries> {
  const res = await fetch(`/api/usage/series?${qsFor(params).toString()}`);
  if (!res.ok) throw new Error(`/api/usage/series failed: ${res.status}`);
  return (await res.json()) as UsageSeries;
}

export async function fetchUsageSummary(params: UsageSeriesParams): Promise<UsageSummary> {
  const res = await fetch(`/api/usage/summary?${qsFor(params).toString()}`);
  if (!res.ok) throw new Error(`/api/usage/summary failed: ${res.status}`);
  return (await res.json()) as UsageSummary;
}

export async function fetchUsageByModel(params: UsageSeriesParams): Promise<ByModelEntry[]> {
  const res = await fetch(`/api/usage/by-model?${qsFor(params).toString()}`);
  if (!res.ok) throw new Error(`/api/usage/by-model failed: ${res.status}`);
  return (await res.json()) as ByModelEntry[];
}

export async function fetchUsageCost(params: UsageSeriesParams): Promise<CostSummary> {
  const res = await fetch(`/api/usage/cost?${qsFor(params).toString()}`);
  if (!res.ok) throw new Error(`/api/usage/cost failed: ${res.status}`);
  return (await res.json()) as CostSummary;
}

export async function fetchUsageCostSeries(params: UsageSeriesParams): Promise<UsageSeries> {
  const res = await fetch(`/api/usage/cost-series?${qsFor(params).toString()}`);
  if (!res.ok) throw new Error(`/api/usage/cost-series failed: ${res.status}`);
  return (await res.json()) as UsageSeries;
}
