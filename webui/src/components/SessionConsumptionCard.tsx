import React from 'react'
import { SessionConsumption } from '../types/api'

interface SessionConsumptionCardProps {
  consumption: SessionConsumption | null
  loading: boolean
  error: string | null
}

const SessionConsumptionCard: React.FC<SessionConsumptionCardProps> = ({ consumption, loading, error }) => {
  const formatCurrency = (value: number): string => {
    return `¥${value.toFixed(2)}`
  }

  const formatNumber = (value: number): string => {
    return value.toLocaleString()
  }

  const formatDuration = (startTime: number): string => {
    const now = Date.now() / 1000
    const duration = now - startTime
    const hours = Math.floor(duration / 3600)
    const minutes = Math.floor((duration % 3600) / 60)
    const seconds = Math.floor(duration % 60)

    if (hours > 0) {
      return `${hours}小时${minutes}分钟`
    } else if (minutes > 0) {
      return `${minutes}分钟${seconds}秒`
    } else {
      return `${seconds}秒`
    }
  }

  const calculateCacheHitRate = (): number => {
    if (!consumption) return 0
    const totalTokens = consumption.total_cache_n + consumption.total_prompt_n
    if (totalTokens === 0) return 0
    return (consumption.total_cache_n / totalTokens) * 100
  }

  if (loading) {
    return (
      <div className="session-consumption-card loading">
        <div className="loading-spinner"></div>
        <span>加载消耗数据中...</span>
      </div>
    )
  }

  if (error) {
    return (
      <div className="session-consumption-card error">
        <div className="error-icon">⚠</div>
        <div>{error}</div>
      </div>
    )
  }

  if (!consumption) {
    return (
      <div className="session-consumption-card">
        <div className="no-data">消耗数据不可用</div>
      </div>
    )
  }

  const cacheHitRate = calculateCacheHitRate()

  return (
    <div className="session-consumption-card">
      <div className="card-header">
        <h3 className="card-title">本次运行消耗</h3>
        <span className="session-duration">运行时长: {formatDuration(consumption.session_start_time)}</span>
      </div>

      <div className="consumption-content">
        <div className="consumption-row main-cost">
          <div className="cost-label">总成本</div>
          <div className="cost-value">{formatCurrency(consumption.total_cost_yuan)}</div>
        </div>

        <div className="consumption-grid">
          <div className="consumption-item">
            <div className="item-label">输入Token</div>
            <div className="item-value">{formatNumber(consumption.total_input_tokens)}</div>
          </div>

          <div className="consumption-item">
            <div className="item-label">输出Token</div>
            <div className="item-value">{formatNumber(consumption.total_output_tokens)}</div>
          </div>

          <div className="consumption-item">
            <div className="item-label">缓存命中</div>
            <div className="item-value">{formatNumber(consumption.total_cache_n)}</div>
          </div>

          <div className="consumption-item">
            <div className="item-label">缓存未命中</div>
            <div className="item-value">{formatNumber(consumption.total_prompt_n)}</div>
          </div>
        </div>

        <div className="cache-hit-rate">
          <div className="rate-label">缓存命中率</div>
          <div className="rate-value">{cacheHitRate.toFixed(1)}%</div>
        </div>
      </div>
    </div>
  )
}

export default SessionConsumptionCard