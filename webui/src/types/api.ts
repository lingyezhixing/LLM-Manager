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

export interface LogStreamOptions {
  onMessage: (data: LogStreamData) => void
  onError?: (error: Event) => void
  onClose?: () => void
}