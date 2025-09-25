import axios from 'axios'
import { HealthResponse, ApiInfoResponse } from '../types/api'

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
  }
}