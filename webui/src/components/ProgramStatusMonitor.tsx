import React, { useState, useEffect, useRef } from 'react'
import { apiService } from '../utils/api'
import { ThroughputResponse, SessionConsumptionResponse, ThroughputDataPoint } from '../types/api'
import TotalThroughputChart from './TotalThroughputChart'
import SessionConsumptionCard from './SessionConsumptionCard'
import ModeThroughputChart from './ModeThroughputChart'

const ProgramStatusMonitor: React.FC = () => {
  const [throughputData, setThroughputData] = useState<ThroughputResponse | null>(null)
  const [sessionConsumption, setSessionConsumption] = useState<SessionConsumptionResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const timeoutRef = useRef<NodeJS.Timeout | null>(null)

  const fetchData = async () => {
    try {
      const endTime = Date.now() / 1000
      const startTime = endTime - 600 // 10分钟前
      const nSamples = 120 // 120个采样点

      const [throughputResponse, consumptionResponse] = await Promise.all([
        apiService.getThroughput(startTime, endTime, nSamples),
        apiService.getSessionConsumption()
      ])

      setThroughputData(throughputResponse)
      setSessionConsumption(consumptionResponse)
      setError(null)
    } catch (err) {
      console.error('Failed to fetch program status data:', err)
      setError('获取程序状态数据失败')
    } finally {
      setLoading(false)
    }
  }

  const startNextRequest = () => {
    timeoutRef.current = setTimeout(() => {
      fetchData().finally(() => {
        startNextRequest()
      })
    }, 5000) // 每5秒更新一次
  }

  useEffect(() => {
    fetchData().finally(() => {
      startNextRequest()
    })

    return () => {
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current)
      }
    }
  }, [])

  const totalThroughputData = throughputData?.data?.time_points || null
  const modeBreakdown = throughputData?.data?.mode_breakdown || {}
  const consumptionData = sessionConsumption?.data?.session_total || null

  const modeEntries = Object.entries(modeBreakdown)

  return (
    <div className="program-status-monitor">
      <h2 className="section-title">程序状态监控</h2>

      {/* 第一行：总吞吐量图 (5/6) + 消耗卡片 (1/6) */}
      <div className="first-row">
        <div className="total-throughput-container">
          <TotalThroughputChart
            data={totalThroughputData}
            loading={loading}
            error={error}
          />
        </div>
        <div className="consumption-card-container">
          <SessionConsumptionCard
            consumption={consumptionData}
            loading={loading}
            error={error}
          />
        </div>
      </div>

      {/* 分模式吞吐量图表 */}
      {modeEntries.length > 0 && (
        <div className="mode-charts-section">
          <div className="mode-charts-grid">
            {modeEntries.map(([modeName, modeData]) => (
              <div key={modeName} className="mode-chart-item">
                <ModeThroughputChart
                  modeName={modeName}
                  data={modeData}
                  loading={loading}
                  error={error}
                />
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

export default ProgramStatusMonitor