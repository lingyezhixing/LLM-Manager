import React, { useState, useEffect } from 'react'
import { apiService } from '../utils/api'
import {
  ModelStatsResponse,
  ModelStatsSummary,
  ModelStatsDataPoint
} from '../types/api'
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  Title,
  Tooltip,
  Legend,
  Filler,
  ArcElement
} from 'chart.js'
import { Line, Pie } from 'react-chartjs-2'

ChartJS.register(
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  Title,
  Tooltip,
  Legend,
  Filler,
  ArcElement
)

interface ModelDetailContentProps {
  selectedModel: string
  timeRange: { start: Date, end: Date } | null
}

const ModelDetailContent: React.FC<ModelDetailContentProps> = ({ selectedModel, timeRange }) => {
  const [modelStats, setModelStats] = useState<ModelStatsResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (timeRange && selectedModel && selectedModel !== '总览') {
      fetchData()
    }
  }, [timeRange, selectedModel])

  const fetchData = async () => {
    if (!timeRange || !selectedModel || selectedModel === '总览') return

    setLoading(true)
    setError(null)

    try {
      const startTime = timeRange.start.getTime() / 1000
      const endTime = timeRange.end.getTime() / 1000
      const nSamples = 100

      const statsData = await apiService.getModelStats(selectedModel, startTime, endTime, nSamples)
      setModelStats(statsData)
    } catch (err) {
      console.error('Failed to fetch model stats:', err)
      setError('获取模型统计数据失败')
    } finally {
      setLoading(false)
    }
  }

  // 智能时间格式化函数
  const formatTime = (timestamp: number): string => {
    // 如果没有时间范围，默认只显示时间
    if (!timeRange) {
      return new Date(timestamp * 1000).toLocaleTimeString('zh-CN', {
        hour: '2-digit',
        minute: '2-digit'
      })
    }

    // 计算时间跨度（秒）
    const durationSec = (timeRange.end.getTime() - timeRange.start.getTime()) / 1000
    const date = new Date(timestamp * 1000)

    // 场景 1: 跨度小于等于 24 小时 -> 显示 "HH:mm"
    if (durationSec <= 86400) {
      return date.toLocaleTimeString('zh-CN', {
        hour: '2-digit',
        minute: '2-digit'
      })
    }
    // 场景 2: 跨度大于 24 小时 且 小于等于 7 天 -> 显示 "MM-DD HH:mm"
    else if (durationSec <= 604800) {
      const month = (date.getMonth() + 1).toString().padStart(2, '0')
      const day = date.getDate().toString().padStart(2, '0')
      const hour = date.getHours().toString().padStart(2, '0')
      const minute = date.getMinutes().toString().padStart(2, '0')
      return `${month}-${day} ${hour}:${minute}`
    }
    // 场景 3: 跨度大于 7 天 -> 显示 "YYYY-MM-DD"
    else {
      const year = date.getFullYear()
      const month = (date.getMonth() + 1).toString().padStart(2, '0')
      const day = date.getDate().toString().padStart(2, '0')
      return `${year}-${month}-${day}`
    }
  }

  const formatNumber = (value: number): string => {
    return value.toLocaleString()
  }

  const formatCost = (value: number): string => {
    return `¥${value.toFixed(4)}`
  }

  const formatTokens = (value: number): string => {
    if (value >= 1000000) {
      return `${(value / 1000000).toFixed(1)}M`
    } else if (value >= 1000) {
      return `${(value / 1000).toFixed(1)}K`
    }
    return value.toString()
  }

  // Token趋势图表数据
  const tokenChartData = {
    labels: modelStats?.data.time_points.map(point => formatTime(point.timestamp)) || [],
    datasets: [
      {
        label: '总Token',
        data: modelStats?.data.time_points.map(point => point.data.total_tokens) || [],
        borderColor: '#3b82f6',
        backgroundColor: 'rgba(59, 130, 246, 0.1)',
        borderWidth: 2,
        fill: true,
        tension: 0.4,
        pointRadius: 0,
        pointHoverRadius: 4,
      },
      {
        label: '输入Token',
        data: modelStats?.data.time_points.map(point => point.data.input_tokens) || [],
        borderColor: '#10b981',
        backgroundColor: 'rgba(16, 185, 129, 0.1)',
        borderWidth: 2,
        fill: false,
        tension: 0.4,
        pointRadius: 0,
        pointHoverRadius: 4,
      },
      {
        label: '输出Token',
        data: modelStats?.data.time_points.map(point => point.data.output_tokens) || [],
        borderColor: '#f59e0b',
        backgroundColor: 'rgba(245, 158, 11, 0.1)',
        borderWidth: 2,
        fill: false,
        tension: 0.4,
        pointRadius: 0,
        pointHoverRadius: 4,
      },
      {
        label: '缓存命中',
        data: modelStats?.data.time_points.map(point => point.data.cache_hit_tokens) || [],
        borderColor: '#8b5cf6',
        backgroundColor: 'rgba(139, 92, 246, 0.1)',
        borderWidth: 2,
        fill: false,
        tension: 0.4,
        pointRadius: 0,
        pointHoverRadius: 4,
      }
    ]
  }

  // 成本趋势图表数据
  const costChartData = {
    labels: modelStats?.data.time_points.map(point => formatTime(point.timestamp)) || [],
    datasets: [
      {
        label: '成本',
        data: modelStats?.data.time_points.map(point => point.data.cost) || [],
        borderColor: '#059669',
        backgroundColor: 'rgba(5, 150, 105, 0.1)',
        borderWidth: 2,
        fill: true,
        tension: 0.4,
        pointRadius: 0,
        pointHoverRadius: 4,
      }
    ]
  }

  // Token构成饼图数据
  const tokenCompositionData = {
    labels: ['输入Token', '输出Token'],
    datasets: [
      {
        data: modelStats ? [
          modelStats.data.summary.total_input_tokens,
          modelStats.data.summary.total_output_tokens
        ] : [],
        backgroundColor: [
          '#10b981',
          '#f59e0b'
        ],
        borderWidth: 2,
        borderColor: '#ffffff'
      }
    ]
  }

  const chartOptions = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: {
        position: 'top' as const,
        align: 'start' as const,
        labels: {
          boxWidth: 12,
          padding: 15,
          font: {
            size: 11
          }
        }
      },
      tooltip: {
        mode: 'index' as const,
        intersect: false,
        backgroundColor: 'rgba(0, 0, 0, 0.8)',
        titleColor: '#fff',
        bodyColor: '#fff',
        borderColor: '#374151',
        borderWidth: 1,
        cornerRadius: 6,
        displayColors: true,
      }
    },
    scales: {
      x: {
        grid: {
          display: false
        },
        ticks: {
          maxTicksLimit: 8,
          font: {
            size: 10
          }
        }
      },
      y: {
        beginAtZero: true,
        grid: {
          color: 'rgba(0, 0, 0, 0.05)'
        },
        ticks: {
          font: {
            size: 10
          }
        }
      }
    },
    interaction: {
      mode: 'nearest' as const,
      axis: 'x' as const,
      intersect: false
    }
  }

  const pieOptions = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: {
        position: 'bottom' as const,
        align: 'center' as const,
        labels: {
          boxWidth: 12,
          padding: 12,
          font: {
            size: 11
          },
          usePointStyle: true,
          pointStyle: 'rect'
        }
      },
      tooltip: {
        backgroundColor: 'rgba(0, 0, 0, 0.8)',
        titleColor: '#fff',
        bodyColor: '#fff',
        borderColor: '#374151',
        borderWidth: 1,
        cornerRadius: 6,
        callbacks: {
          label: function(context: any) {
            const label = context.label || '';
            const value = context.parsed || 0;
            const total = context.dataset.data.reduce((a: number, b: number) => a + b, 0);
            const percentage = ((value / total) * 100).toFixed(1);
            return `${label}: ${formatTokens(value)} (${percentage}%)`;
          }
        }
      }
    }
  }

  if (loading) {
    return (
      <div className="content-placeholder">
        <div className="loading-spinner"></div>
        <span>加载模型数据中...</span>
      </div>
    )
  }

  if (error) {
    return (
      <div className="content-placeholder">
        <div className="error-icon">⚠</div>
        <span>{error}</span>
      </div>
    )
  }

  if (!timeRange || !selectedModel || selectedModel === '总览') {
    return (
      <div className="content-placeholder">
        <p>请选择具体的模型以查看详情数据</p>
      </div>
    )
  }

  const summary = modelStats?.data.summary

  return (
    <div className="model-detail-content">
      {/* 模型统计概览 */}
      <div className="model-summary-section">
        <h3 className="section-title">{selectedModel} - 统计概览</h3>
        <div className="summary-cards">
          <div className="summary-card">
            <div className="card-header">
              <span className="card-label">总请求数</span>
            </div>
            <div className="card-value">{summary?.request_count.toLocaleString() || 0}</div>
          </div>
          <div className="summary-card">
            <div className="card-header">
              <span className="card-label">总成本</span>
            </div>
            <div className="card-value">{formatCost(summary?.total_cost || 0)}</div>
          </div>
          <div className="summary-card">
            <div className="card-header">
              <span className="card-label">总Token</span>
            </div>
            <div className="card-value">{formatTokens(summary?.total_tokens || 0)}</div>
          </div>
          <div className="summary-card">
            <div className="card-header">
              <span className="card-label">输出Token</span>
            </div>
            <div className="card-value">{formatTokens(summary?.total_output_tokens || 0)}</div>
          </div>
          <div className="summary-card">
            <div className="card-header">
              <span className="card-label">输入Token</span>
            </div>
            <div className="card-value">{formatTokens(summary?.total_input_tokens || 0)}</div>
          </div>
          <div className="summary-card">
            <div className="card-header">
              <span className="card-label">缓存命中Token</span>
            </div>
            <div className="card-value">{formatTokens(summary?.total_cache_n || 0)}</div>
          </div>
          <div className="summary-card cache-rate-card">
            <div className="card-header">
              <span className="card-label">缓存命中率</span>
            </div>
            <div className="card-value">
              {summary && summary.total_input_tokens > 0 ?
                ((summary.total_cache_n / summary.total_input_tokens) * 100).toFixed(1) : '0.0'}%
            </div>
          </div>
        </div>
      </div>

      {/* Token趋势图表 */}
      <div className="chart-section">
        <h3 className="section-title">Token消耗趋势</h3>
        <div className="chart-container">
          <Line data={tokenChartData} options={chartOptions} />
        </div>
      </div>

      {/* 成本趋势图表 */}
      <div className="chart-section">
        <h3 className="section-title">成本趋势</h3>
        <div className="chart-container">
          <Line data={costChartData} options={chartOptions} />
        </div>
      </div>

      </div>
  )
}

export default ModelDetailContent