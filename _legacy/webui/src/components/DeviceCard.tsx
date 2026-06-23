import React from 'react'
import { DeviceStatus } from '../types/api'

interface DeviceCardProps {
  deviceName: string
  deviceStatus: DeviceStatus
}

const DeviceCard: React.FC<DeviceCardProps> = ({ deviceName, deviceStatus }) => {
  const { online, info } = deviceStatus

  if (!online) {
    return (
      <div className="device-card offline">
        <div className="device-header">
          <h3 className="device-name">{deviceName}</h3>
          <span className="device-status offline">离线</span>
        </div>
        <div className="device-offline-message">设备不可用</div>
      </div>
    )
  }

  // 计算内存使用率
  const memoryUsagePercentage = (info.used_memory_mb / info.total_memory_mb) * 100

  // 获取指示条颜色
  const getIndicatorColor = (percentage: number): string => {
    if (percentage < 40) return '#10b981' // 绿色
    if (percentage < 80) return '#f59e0b' // 黄色
    return '#ef4444' // 红色
  }

  const memoryColor = getIndicatorColor(memoryUsagePercentage)
  const usageColor = getIndicatorColor(info.usage_percentage)

  return (
    <div className="device-card">
      <div className="device-header">
        <h3 className="device-name">{deviceName}</h3>
        <span className="device-status online">在线</span>
      </div>

      <div className="device-main">
        <div className="device-info">
          <div className="info-row">
            <span className="info-label">类型:</span>
            <span className="info-value">{info.device_type}</span>
          </div>
          <div className="info-row">
            <span className="info-label">内存:</span>
            <span className="info-value">{info.memory_type}</span>
          </div>
          {info.temperature_celsius !== null && (
            <div className="info-row">
              <span className="info-label">温度:</span>
              <span className="info-value">{info.temperature_celsius}°C</span>
            </div>
          )}
        </div>

        <div className="device-indicators">
          <div className="indicator">
            <div className="indicator-header">
              <span className="indicator-label">内存</span>
              <span className="indicator-value">{memoryUsagePercentage.toFixed(1)}%</span>
            </div>
            <div className="indicator-bar">
              <div
                className="indicator-fill memory-fill"
                style={{
                  width: `${memoryUsagePercentage}%`,
                  backgroundColor: memoryColor,
                  transition: 'width 0.3s ease, background-color 0.3s ease'
                }}
              />
            </div>
            <div className="indicator-details">
              <span className="memory-used">{info.used_memory_mb}MB</span>
              <span className="memory-total">/ {info.total_memory_mb}MB</span>
            </div>
          </div>

          <div className="indicator">
            <div className="indicator-header">
              <span className="indicator-label">占用率</span>
              <span className="indicator-value">{info.usage_percentage.toFixed(1)}%</span>
            </div>
            <div className="indicator-bar">
              <div
                className="indicator-fill usage-fill"
                style={{
                  width: `${info.usage_percentage}%`,
                  backgroundColor: usageColor,
                  transition: 'width 0.3s ease, background-color 0.3s ease'
                }}
              />
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

export default DeviceCard