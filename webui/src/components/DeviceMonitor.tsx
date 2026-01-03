import React, { useState, useEffect, useRef } from 'react'
import { apiService } from '../utils/api'
import { DevicesResponse } from '../types/api'
import DeviceCard from './DeviceCard'

const DeviceMonitor: React.FC = () => {
  const [devicesData, setDevicesData] = useState<DevicesResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const timeoutRef = useRef<NodeJS.Timeout | null>(null)

  useEffect(() => {
    const fetchDevices = async () => {
      try {
        const data = await apiService.getDevicesInfo()
        setDevicesData(data)
        setError(null)
      } catch (err) {
        console.error('Failed to fetch devices info:', err)
        setError('获取设备信息失败')
      } finally {
        setLoading(false)
      }
    }

    const startNextRequest = () => {
      timeoutRef.current = setTimeout(() => {
        fetchDevices().finally(() => {
          startNextRequest()
        })
      }, 500) // [按需监控优化] 前端0.5秒轮询，后端1秒更新，2倍频率保证不漏信息
    }

    // 首次请求
    fetchDevices().finally(() => {
      startNextRequest()
    })

    return () => {
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current)
      }
    }
  }, [])

  if (loading) {
    return (
      <div className="device-monitor">
        <h2 className="section-title">设备状态监控</h2>
        <div className="loading">
          <div className="loading-spinner"></div>
          <span>加载设备信息中...</span>
        </div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="device-monitor">
        <h2 className="section-title">设备状态监控</h2>
        <div className="error">
          <div className="error-icon">⚠</div>
          <div>{error}</div>
        </div>
      </div>
    )
  }

  if (!devicesData || !devicesData.success) {
    return (
      <div className="device-monitor">
        <h2 className="section-title">设备状态监控</h2>
        <div className="error">设备数据不可用</div>
      </div>
    )
  }

  const deviceEntries = Object.entries(devicesData.devices)

  if (deviceEntries.length === 0) {
    return (
      <div className="device-monitor">
        <h2 className="section-title">设备状态监控</h2>
        <div className="no-devices">未发现可用设备</div>
      </div>
    )
  }

  return (
    <div className="device-monitor">
      <h2 className="section-title">设备状态监控</h2>
      <div className="device-cards-container">
        {deviceEntries.map(([deviceName, deviceStatus]) => (
          <DeviceCard
            key={deviceName}
            deviceName={deviceName}
            deviceStatus={deviceStatus}
          />
        ))}
      </div>
    </div>
  )
}

export default DeviceMonitor