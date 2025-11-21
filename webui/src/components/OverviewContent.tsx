import React, { useState, useEffect } from 'react'
import { apiService } from '../utils/api'
import {
  UsageSummaryResponse,
  TokenTrendsResponse,
  CostTrendsResponse,
  TokenTrendDataPoint,
  CostTrendDataPoint
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

interface OverviewContentProps {
  timeRange: { start: Date, end: Date } | null
}

const OverviewContent: React.FC<OverviewContentProps> = ({ timeRange }) => {
  const [usageSummary, setUsageSummary] = useState<UsageSummaryResponse | null>(null)
  const [tokenTrends, setTokenTrends] = useState<TokenTrendsResponse | null>(null)
  const [costTrends, setCostTrends] = useState<CostTrendsResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (timeRange) {
      fetchData()
    }
  }, [timeRange])

  const fetchData = async () => {
    if (!timeRange) return

    setLoading(true)
    setError(null)

    try {
      const startTime = timeRange.start.getTime() / 1000
      const endTime = timeRange.end.getTime() / 1000
      const nSamples = 100

      const [summaryData, tokenData, costData] = await Promise.all([
        apiService.getUsageSummary(startTime, endTime),
        apiService.getTokenTrends(startTime, endTime, nSamples),
        apiService.getCostTrends(startTime, endTime, nSamples)
      ])

      setUsageSummary(summaryData)
      setTokenTrends(tokenData)
      setCostTrends(costData)
    } catch (err) {
      console.error('Failed to fetch overview data:', err)
      setError('获取数据失败')
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
    return value.toFixed(1)
  }

  // Token吞吐量图表数据
  const tokenChartData = {
    labels: tokenTrends?.data.time_points.map(point => formatTime(point.timestamp)) || [],
    datasets: [
      {
        label: '总Token',
        data: tokenTrends?.data.time_points.map(point => point.data.total_tokens) || [],
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
        data: tokenTrends?.data.time_points.map(point => point.data.input_tokens) || [],
        borderColor: '#10b981',
        backgroundColor: 'rgba(16, 185, 129, 0.1)',
        borderWidth: 2,
        fill: true,
        tension: 0.4,
        pointRadius: 0,
        pointHoverRadius: 4,
      },
      {
        label: '输出Token',
        data: tokenTrends?.data.time_points.map(point => point.data.output_tokens) || [],
        borderColor: '#f59e0b',
        backgroundColor: 'rgba(245, 158, 11, 0.1)',
        borderWidth: 2,
        fill: true,
        tension: 0.4,
        pointRadius: 0,
        pointHoverRadius: 4,
      },
      {
        label: '缓存命中',
        data: tokenTrends?.data.time_points.map(point => point.data.cache_hit_tokens) || [],
        borderColor: '#8b5cf6',
        backgroundColor: 'rgba(139, 92, 246, 0.1)',
        borderWidth: 2,
        fill: true,
        tension: 0.4,
        pointRadius: 0,
        pointHoverRadius: 4,
      },
      {
        label: '缓存未命中',
        data: tokenTrends?.data.time_points.map(point => point.data.cache_miss_tokens) || [],
        borderColor: '#ef4444',
        backgroundColor: 'rgba(239, 68, 68, 0.1)',
        borderWidth: 2,
        fill: true,
        tension: 0.4,
        pointRadius: 0,
        pointHoverRadius: 4,
      }
    ]
  }

  // Token消费比例饼图数据
  const tokenPieData = {
    labels: Object.keys(usageSummary?.data.mode_summary || {}),
    datasets: [
      {
        data: Object.values(usageSummary?.data.mode_summary || {}).map((item: any) => item.total_tokens),
        backgroundColor: [
          '#3b82f6',
          '#10b981',
          '#f59e0b',
          '#8b5cf6',
          '#ef4444'
        ],
        borderWidth: 2,
        borderColor: '#ffffff'
      }
    ]
  }

  // 资金消耗图表数据
  const costChartData = {
    labels: costTrends?.data.time_points.map(point => formatTime(point.timestamp)) || [],
    datasets: [
      {
        label: '总成本',
        data: costTrends?.data.time_points.map(point => point.data.cost) || [],
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

  // 资金消耗比例饼图数据
  const costPieData = {
    labels: Object.keys(usageSummary?.data.mode_summary || {}),
    datasets: [
      {
        data: Object.values(usageSummary?.data.mode_summary || {}).map((item: any) => item.total_cost),
        backgroundColor: [
          '#3b82f6',
          '#10b981',
          '#f59e0b',
          '#8b5cf6',
          '#ef4444'
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
            return `${label}: ${value.toLocaleString()} (${percentage}%)`;
          }
        }
      }
    }
  }

  if (loading) {
    return (
      <div className="content-placeholder">
        <div className="loading-spinner"></div>
        <span>加载数据中...</span>
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

  if (!timeRange) {
    return (
      <div className="content-placeholder">
        <p>请选择时间范围以查看数据</p>
      </div>
    )
  }

  return (
    <div className="overview-content">
      {/* 第一行：4:1 布局 - Token吞吐量和比例 */}
      <div className="overview-row">
        <div className="chart-container-large">
          <div className="throughput-chart">
            <div className="chart-header">
              <h3 className="chart-title">Token吞吐量趋势</h3>
            </div>
            <div style={{ height: '100%' }}>
              <Line data={tokenChartData} options={chartOptions} />
            </div>
          </div>
        </div>
        <div className="chart-container-small">
          <div className="throughput-chart">
            <div className="chart-header">
              <h3 className="chart-title">Token消费比例</h3>
            </div>
            <div style={{ height: '100%' }}>
              <Pie data={tokenPieData} options={pieOptions} />
            </div>
          </div>
        </div>
      </div>

      {/* 第二行：4:1 布局 - 资金消耗和比例 */}
      <div className="overview-row">
        <div className="chart-container-large">
          <div className="throughput-chart">
            <div className="chart-header">
              <h3 className="chart-title">资金消耗趋势</h3>
            </div>
            <div style={{ height: '100%' }}>
              <Line data={costChartData} options={chartOptions} />
            </div>
          </div>
        </div>
        <div className="chart-container-small">
          <div className="throughput-chart">
            <div className="chart-header">
              <h3 className="chart-title">资金消耗比例</h3>
            </div>
            <div style={{ height: '100%' }}>
              <Pie data={costPieData} options={pieOptions} />
            </div>
          </div>
        </div>
      </div>

      {/* 动态模型类别图表行 - 1:1 布局 */}
      {tokenTrends?.data.mode_breakdown && Object.keys(tokenTrends.data.mode_breakdown).length > 0 && (
        Object.entries(tokenTrends.data.mode_breakdown).map(([modeName, modeData]) => (
          <div key={modeName} className="model-detail-row">
            <div className="model-detail-chart-container">
              <div className="throughput-chart">
                <div className="chart-header">
                  <h3 className="chart-title">{modeName} - Token吞吐量</h3>
                </div>
                <div style={{ height: '100%' }}>
                  <Line data={{
                    labels: modeData.map(point => formatTime(point.timestamp)),
                    datasets: [
                      {
                        label: '总Token',
                        data: modeData.map(point => point.data.total_tokens),
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
                        data: modeData.map(point => point.data.input_tokens),
                        borderColor: '#10b981',
                        backgroundColor: 'rgba(16, 185, 129, 0.1)',
                        borderWidth: 2,
                        fill: true,
                        tension: 0.4,
                        pointRadius: 0,
                        pointHoverRadius: 4,
                      },
                      {
                        label: '输出Token',
                        data: modeData.map(point => point.data.output_tokens),
                        borderColor: '#f59e0b',
                        backgroundColor: 'rgba(245, 158, 11, 0.1)',
                        borderWidth: 2,
                        fill: true,
                        tension: 0.4,
                        pointRadius: 0,
                        pointHoverRadius: 4,
                      }
                    ]
                  }} options={chartOptions} />
                </div>
              </div>
            </div>

            <div className="model-detail-chart-container">
              <div className="throughput-chart">
                <div className="chart-header">
                  <h3 className="chart-title">{modeName} - 资金消耗</h3>
                </div>
                <div style={{ height: '100%' }}>
                  <Line data={{
                    labels: modeData.map(point => formatTime(point.timestamp)),
                    datasets: [
                      {
                        label: '成本',
                        data: costTrends?.data.mode_breakdown?.[modeName]?.map(point => point.data.cost) || [],
                        borderColor: '#059669',
                        backgroundColor: 'rgba(5, 150, 105, 0.1)',
                        borderWidth: 2,
                        fill: true,
                        tension: 0.4,
                        pointRadius: 0,
                        pointHoverRadius: 4,
                      }
                    ]
                  }} options={chartOptions} />
                </div>
              </div>
            </div>
          </div>
        ))
      )}
    </div>
  )
}

export default OverviewContent