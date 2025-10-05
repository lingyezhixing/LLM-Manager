import React from 'react'
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  Title,
  Tooltip,
  Legend,
  Filler
} from 'chart.js'
import { Line } from 'react-chartjs-2'
import { ThroughputDataPoint } from '../types/api'

ChartJS.register(
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  Title,
  Tooltip,
  Legend,
  Filler
)

interface ModeThroughputChartProps {
  modeName: string
  data: ThroughputDataPoint[] | null
  loading: boolean
  error: string | null
}

const ModeThroughputChart: React.FC<ModeThroughputChartProps> = ({ modeName, data, loading, error }) => {
  const formatTime = (timestamp: number): string => {
    return new Date(timestamp * 1000).toLocaleTimeString('zh-CN', {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit'
    })
  }

  const formatNumber = (value: number): string => {
    return value.toFixed(1)
  }

  const getModeColor = (mode: string): string => {
    const colors = {
      'Chat': '#3b82f6',
      'Embedding': '#10b981',
      'Base': '#f59e0b',
      'Reranker': '#8b5cf6',
      'Default': '#6b7280'
    }
    return colors[mode as keyof typeof colors] || colors.Default
  }

  const modeColor = getModeColor(modeName)

  const chartData = {
    labels: data?.map(point => formatTime(point.timestamp)) || [],
    datasets: [
      {
        label: '总Token吞吐量',
        data: data?.map(point => point.data.total_tokens_per_sec) || [],
        borderColor: modeColor,
        backgroundColor: modeColor + '20',
        borderWidth: 2,
        fill: true,
        tension: 0.4,
        pointRadius: 0,
        pointHoverRadius: 4,
      },
      {
        label: '输入Token吞吐量',
        data: data?.map(point => point.data.input_tokens_per_sec) || [],
        borderColor: '#10b981',
        backgroundColor: 'rgba(16, 185, 129, 0.1)',
        borderWidth: 2,
        fill: true,
        tension: 0.4,
        pointRadius: 0,
        pointHoverRadius: 4,
      },
      {
        label: '输出Token吞吐量',
        data: data?.map(point => point.data.output_tokens_per_sec) || [],
        borderColor: '#f59e0b',
        backgroundColor: 'rgba(245, 158, 11, 0.1)',
        borderWidth: 2,
        fill: true,
        tension: 0.4,
        pointRadius: 0,
        pointHoverRadius: 4,
      },
      {
        label: '缓存命中吞吐量',
        data: data?.map(point => point.data.cache_hit_tokens_per_sec) || [],
        borderColor: '#8b5cf6',
        backgroundColor: 'rgba(139, 92, 246, 0.1)',
        borderWidth: 2,
        fill: true,
        tension: 0.4,
        pointRadius: 0,
        pointHoverRadius: 4,
      },
      {
        label: '缓存未命中吞吐量',
        data: data?.map(point => point.data.cache_miss_tokens_per_sec) || [],
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

  const options = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: {
        position: 'top' as const,
        align: 'start' as const,
        labels: {
          boxWidth: 10,
          padding: 10,
          font: {
            size: 10
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
        cornerRadius: 4,
        displayColors: true,
        callbacks: {
          label: function(context: any) {
            return `${context.dataset.label}: ${formatNumber(context.parsed.y)} tokens/sec`
          }
        }
      }
    },
    scales: {
      x: {
        grid: {
          display: false
        },
        ticks: {
          maxTicksLimit: 6,
          font: {
            size: 9
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
            size: 9
          },
          callback: function(value: any) {
            return formatNumber(value)
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

  if (loading) {
    return (
      <div className="mode-throughput-chart loading">
        <div className="loading-spinner"></div>
        <span>加载{modeName}模式数据中...</span>
      </div>
    )
  }

  if (error) {
    return (
      <div className="mode-throughput-chart error">
        <div className="error-icon">⚠</div>
        <div>{error}</div>
      </div>
    )
  }

  if (!data || data.length === 0) {
    return (
      <div className="mode-throughput-chart">
        <div className="no-data">{modeName}模式数据不可用</div>
      </div>
    )
  }

  return (
    <div className="mode-throughput-chart">
      <div className="chart-header">
        <h3 className="chart-title">{modeName}模型吞吐量</h3>
      </div>
      <div className="chart-container">
        <Line data={chartData} options={options} />
      </div>
    </div>
  )
}

export default ModeThroughputChart