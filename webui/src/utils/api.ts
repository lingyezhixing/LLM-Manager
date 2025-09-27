import axios from 'axios'
import { HealthResponse, ApiInfoResponse, DevicesResponse, ThroughputResponse, SessionConsumptionResponse } from '../types/api'

const API_BASE_URL = '/api'

const api = axios.create({
  baseURL: API_BASE_URL,
  timeout: 5000,
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
      console.error('Health check failed:', error)
      throw error
    }
  },

  async getApiInfo(): Promise<ApiInfoResponse> {
    try {
      const response = await api.get('/')
      return response.data
    } catch (error) {
      console.error('API info failed:', error)
      throw error
    }
  },

  async getDevicesInfo(): Promise<DevicesResponse> {
    try {
      const response = await api.get('/api/devices/info')
      return response.data
    } catch (error) {
      console.error('Devices info failed:', error)
      throw error
    }
  },

  async getThroughput(startTime: number, endTime: number, nSamples: number): Promise<ThroughputResponse> {
    try {
      const response = await api.get(`/api/metrics/throughput/${startTime}/${endTime}/${nSamples}`)
      return response.data
    } catch (error) {
      console.error('Throughput data failed:', error)
      throw error
    }
  },

  async getSessionConsumption(): Promise<SessionConsumptionResponse> {
    try {
      const response = await api.get('/api/metrics/throughput/current-session')
      return response.data
    } catch (error) {
      console.error('Session consumption failed:', error)
      throw error
    }
  }
}