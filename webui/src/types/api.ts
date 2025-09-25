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