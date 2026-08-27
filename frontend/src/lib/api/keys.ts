import type { UsageSeriesParams } from "./usage";

/** TanStack Query key 单源工厂:全部 key 经此构造,禁散落数组字面量。
 *  形状:[域, ...参数](域名与 API 模块名对齐:config/data/logs/models/providers/usage/tools/update)。 */
export const qk = {
  config: ["config"] as const,
  restartStatus: ["restart-status"] as const,
  systemInfo: ["system", "info"] as const,
  health: ["health"] as const,
  updateStatus: ["update", "status"] as const,
  modelDefs: ["model-defs"] as const,
  modelDef: (name: string) => ["model-defs", name] as const,
  providerDefs: ["provider-defs"] as const,
  providerDef: (name: string) => ["provider-defs", name] as const,
  sessionsList: (tab: string, model: string) => ["sessions", "list", tab, model] as const,
  sessionModelOptions: ["sessions", "model-options"] as const,
  sessionUsage: ["usage", "session"] as const,
  usageSummary: (params: UsageSeriesParams) => ["usage", "summary", params] as const,
  usageSeries: (params: UsageSeriesParams) => ["usage", "series", params] as const,
  usageByModel: (params: UsageSeriesParams) => ["usage", "by-model", params] as const,
  usageCost: (params: UsageSeriesParams) => ["usage", "cost", params] as const,
  usageCostSeries: (params: UsageSeriesParams) => ["usage", "cost-series", params] as const,
  storageStats: ["data", "storage-stats"] as const,
  orphaned: ["data", "orphaned"] as const,
  deviceNames: ["device-names"] as const,
  claudeCurrent: ["tools", "claude", "current"] as const,
} as const;
