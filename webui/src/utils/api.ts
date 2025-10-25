import axios from 'axios'
import {
  HealthResponse,
  ApiInfoResponse,
  DevicesResponse,
  ThroughputResponse,
  SessionConsumptionResponse,
  OpenAIModelsResponse,
  ModelsResponse,
  ModelActionResponse,
  LogStreamData,
  LogStreamOptions,
  UsageSummaryResponse,
  TokenTrendsResponse,
  CostTrendsResponse
} from '../types/api'

const API_BASE_URL = ''

const api = axios.create({
  baseURL: API_BASE_URL,
  timeout: 15000, // Increased to 15 seconds for model info endpoints
  headers: {
    'Content-Type': 'application/json'
  }
})

export const apiService = {
  async getHealth(): Promise<HealthResponse> {
    try {
      const response = await api.get('/health')
      return response.data
    } catch (error) {
      console.error('Health check failed:', error instanceof Error ? error.message : String(error))
      throw error
    }
  },

  async getApiInfo(): Promise<ApiInfoResponse> {
    try {
      // 暂时禁用API信息获取，避免影响其他功能
      console.log('API info temporarily disabled to avoid proxy conflicts')
      return {
        message: "LLM-Manager API Server",
        version: "1.0.0",
        models_url: "/v1/models"
      }
    } catch (error) {
      console.error('API info failed:', error instanceof Error ? error.message : String(error))
      throw error
    }
  },

  async getDevicesInfo(): Promise<DevicesResponse> {
    try {
      const response = await api.get('/api/devices/info')
      return response.data
    } catch (error) {
      console.error('Devices info failed:', error instanceof Error ? error.message : String(error))
      throw error
    }
  },

  async getThroughput(startTime: number, endTime: number, nSamples: number): Promise<ThroughputResponse> {
    try {
      const response = await api.get(`/api/metrics/throughput/${startTime}/${endTime}/${nSamples}`)
      return response.data
    } catch (error) {
      console.error('Throughput data failed:', error instanceof Error ? error.message : String(error))
      throw error
    }
  },

  async getSessionConsumption(): Promise<SessionConsumptionResponse> {
    try {
      const response = await api.get('/api/metrics/throughput/current-session')
      return response.data
    } catch (error) {
      console.error('Session consumption failed:', error instanceof Error ? error.message : String(error))
      throw error
    }
  },

  // Model Management APIs
  async getModels(): Promise<OpenAIModelsResponse> {
    try {
      const response = await api.get('/v1/models')
      return response.data
    } catch (error) {
      console.error('Get models failed:', error instanceof Error ? error.message : String(error))
      throw error
    }
  },

  async getModelsInfo(): Promise<ModelsResponse> {
    try {
      const response = await api.get('/api/models/all-models/info')
      return response.data
    } catch (error) {
      console.error('Get models info failed:', error instanceof Error ? error.message : String(error))
      throw error
    }
  },

  async startModel(modelAlias: string): Promise<ModelActionResponse> {
    try {
      const response = await api.post(`/api/models/${modelAlias}/start`, {}, {
        timeout: 0 // 无超时限制，等待模型启动完成
      })
      return response.data
    } catch (error) {
      console.error('Start model failed:', error instanceof Error ? error.message : String(error))
      throw error
    }
  },

  async stopModel(modelAlias: string): Promise<ModelActionResponse> {
    try {
      const response = await api.post(`/api/models/${modelAlias}/stop`, {}, {
        timeout: 0 // 无超时限制，等待模型停止完成
      })
      return response.data
    } catch (error) {
      console.error('Stop model failed:', error instanceof Error ? error.message : String(error))
      throw error
    }
  },

  createLogStream(modelAlias: string, options: LogStreamOptions): () => void {
    // EventSource不支持代理，使用fetch + stream替代
    const controller = new AbortController()
    const signal = controller.signal

    const connectStream = async () => {
      try {
        const response = await fetch('/api/models/' + modelAlias + '/logs/stream', {
          headers: {
            'Accept': 'text/event-stream',
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
          },
          signal
        })

        if (!response.ok) {
          throw new Error(`HTTP error! status: ${response.status}`)
        }

        const reader = response.body?.getReader()
        const decoder = new TextDecoder()

        if (!reader) {
          throw new Error('Failed to get stream reader')
        }

        let buffer = ''

        while (true) {
          const { done, value } = await reader.read()
          if (done) break

          buffer += decoder.decode(value, { stream: true })
          const lines = buffer.split('\n')
          buffer = lines.pop() || '' // 保留最后一行（可能不完整）

          for (const line of lines) {
            if (line.trim() === '') continue

            // 处理SSE格式
            if (line.startsWith('data: ')) {
              const jsonStr = line.substring(6)
              try {
                const data: LogStreamData = JSON.parse(jsonStr)
                options.onMessage(data)
              } catch (error) {
                console.error('Failed to parse log stream data:', error instanceof Error ? error.message : String(error), 'Raw data:', jsonStr)
              }
            }
          }
        }
      } catch (error) {
        if (error instanceof Error && error.name === 'AbortError') {
          console.log('Stream aborted')
        } else {
          console.error('Log stream error:', error instanceof Error ? error.message : String(error))
          if (options.onError) {
            options.onError(error as any)
          }
        }
      }
    }

    connectStream()

    // Return cleanup function
    return () => {
      controller.abort()
    }
  },

  // Analytics APIs
  async getUsageSummary(startTime: number, endTime: number): Promise<UsageSummaryResponse> {
    try {
      const response = await api.get(`/api/analytics/usage-summary/${startTime}/${endTime}`)
      return response.data
    } catch (error) {
      console.error('Usage summary failed:', error instanceof Error ? error.message : String(error))
      throw error
    }
  },

  async getTokenTrends(startTime: number, endTime: number, nSamples: number): Promise<TokenTrendsResponse> {
    try {
      const response = await api.get(`/api/analytics/token-trends/${startTime}/${endTime}/${nSamples}`)
      return response.data
    } catch (error) {
      console.error('Token trends failed:', error instanceof Error ? error.message : String(error))
      throw error
    }
  },

  async getCostTrends(startTime: number, endTime: number, nSamples: number): Promise<CostTrendsResponse> {
    try {
      const response = await api.get(`/api/analytics/cost-trends/${startTime}/${endTime}/${nSamples}`)
      return response.data
    } catch (error) {
      console.error('Cost trends failed:', error instanceof Error ? error.message : String(error))
      throw error
    }
  }
}