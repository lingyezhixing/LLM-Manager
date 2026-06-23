export interface HealthResponse {
  status: 'healthy' | 'unhealthy'
  models_count: number
  running_models: number
}

export interface ApiInfoResponse {
  message: string
  version: string
  models_url: string
}

export interface DeviceInfo {
  device_type: string
  memory_type: string
  total_memory_mb: number
  available_memory_mb: number
  used_memory_mb: number
  usage_percentage: number
  temperature_celsius: number | null
}

export interface DeviceStatus {
  online: boolean
  info: DeviceInfo
}

export interface DevicesResponse {
  success: boolean
  devices: Record<string, DeviceStatus>
}

export interface ThroughputDataPoint {
  timestamp: number
  data: {
    input_tokens_per_sec: number
    output_tokens_per_sec: number
    total_tokens_per_sec: number
    cache_hit_tokens_per_sec: number
    cache_miss_tokens_per_sec: number
  }
}

export interface ThroughputResponse {
  success: boolean
  data: {
    time_points: ThroughputDataPoint[]
    mode_breakdown: Record<string, ThroughputDataPoint[]>
  }
}

export interface SessionConsumption {
  total_cost_yuan: number
  total_input_tokens: number
  total_output_tokens: number
  total_cache_n: number
  total_prompt_n: number
  session_start_time: number
}

export interface SessionConsumptionResponse {
  success: boolean
  data: {
    session_total: SessionConsumption
  }
}

// Model Management Types
export interface OpenAIModel {
  id: string
  object: 'model'
  created: number
  owned_by: string
  aliases: string[]
  mode: 'Chat' | 'Base' | 'Embedding' | 'Reranker'
}

export interface OpenAIModelsResponse {
  object: 'list'
  data: OpenAIModel[]
}

export interface ModelInfo {
  model_name: string
  aliases: string[]
  status: 'stopped' | 'starting' | 'routing' | 'failed'
  pid: number | null
  idle_time_sec: string
  mode: 'Chat' | 'Base' | 'Embedding' | 'Reranker'
  is_available: boolean
  current_bat_path: string
  config_source: string
  failure_reason: string | null
  pending_requests: number
}

export interface ModelsResponse {
  success: boolean
  models: Record<string, ModelInfo>
  total_models: number
  running_models: number
  total_pending_requests: number
}

export interface ModelActionResponse {
  success: boolean
  message: string
}

export interface LogStreamData {
  type: 'historical' | 'historical_complete' | 'realtime' | 'stream_end' | 'error'
  log?: {
    timestamp: number
    message: string
  } | string
  message?: string
}

// Analytics API Types
export interface UsageSummaryData {
  total_tokens: number
  total_cost: number
}

export interface UsageSummaryResponse {
  success: boolean
  data: {
    mode_summary: Record<string, UsageSummaryData>
    overall_summary: UsageSummaryData
  }
}

export interface TokenTrendData {
  input_tokens: number
  output_tokens: number
  total_tokens: number
  cache_hit_tokens: number
  cache_miss_tokens: number
}

export interface TokenTrendDataPoint {
  timestamp: number
  data: TokenTrendData
}

export interface TokenTrendsResponse {
  success: boolean
  data: {
    time_points: TokenTrendDataPoint[]
    mode_breakdown: Record<string, TokenTrendDataPoint[]>
  }
}

export interface CostTrendData {
  cost: number
}

export interface CostTrendDataPoint {
  timestamp: number
  data: CostTrendData
}

export interface CostTrendsResponse {
  success: boolean
  data: {
    time_points: CostTrendDataPoint[]
    mode_breakdown: Record<string, CostTrendDataPoint[]>
  }
}

// Single Model Stats API Types
export interface ModelStatsSummary {
  total_input_tokens: number
  total_output_tokens: number
  total_tokens: number
  total_cache_n: number
  total_prompt_n: number
  total_cost: number
  request_count: number
}

export interface ModelStatsDataPoint {
  timestamp: number
  data: {
    input_tokens: number
    output_tokens: number
    total_tokens: number
    cache_hit_tokens: number
    cache_miss_tokens: number
    cost: number
  }
}

export interface ModelStatsResponse {
  success: boolean
  data: {
    model_name: string
    summary: ModelStatsSummary
    time_points: ModelStatsDataPoint[]
  }
}

// Billing API Types
export interface TierPricing {
  tier_index: number
  min_input_tokens: number
  max_input_tokens: number
  min_output_tokens: number
  max_output_tokens: number
  input_price: number
  output_price: number
  support_cache: boolean
  cache_write_price: number
  cache_read_price: number
}

export interface ModelPricingResponse {
  success: boolean
  data: {
    model_name: string
    pricing_type: 'tier' | 'hourly'
    tier_pricing?: TierPricing[]
    hourly_price?: number
  }
}

// Data Management API Types
export interface OrphanedModelsResponse {
  success: boolean
  data: {
    orphaned_models: string[]
    count: number
  }
}

export interface ModelDataStats {
  request_count: number
  has_runtime_data: boolean
  has_billing_data: boolean
  error?: string
}

export interface StorageStatsResponse {
  success: boolean
  data: {
    database_exists: boolean
    database_size_mb: number
    total_models_with_data: number
    total_requests: number
    models_data: Record<string, ModelDataStats>
  }
}

export interface DeleteModelDataResponse {
  success: boolean
  message: string
}