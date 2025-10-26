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

interface TotalThroughputChartProps {
  data: ThroughputDataPoint[] | null
  loading: boolean
  error: string | null
}

const TotalThroughputChart: React.FC<TotalThroughputChartProps> = ({ data, loading, error }) => {
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

  const chartData = {
    labels: data?.map(point => formatTime(point.timestamp)) || [],
    datasets: [
      {
        label: '总Token吞吐量',
        data: data?.map(point => point.data.total_tokens_per_sec) || [],
        borderColor: '#3b82f6',
        backgroundColor: 'rgba(59, 130, 246, 0.1)',
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
      <div className="throughput-chart loading">
        <div className="loading-spinner"></div>
        <span>加载吞吐量数据中...</span>
      </div>
    )
  }

  if (error) {
    return (
      <div className="throughput-chart error">
        <div className="error-icon">⚠</div>
        <div>{error}</div>
      </div>
    )
  }

  if (!data || data.length === 0) {
    return (
      <div className="throughput-chart">
        <div className="no-data">吞吐量数据不可用</div>
      </div>
    )
  }

  return (
    <div className="throughput-chart">
      <div className="chart-header">
        <h3 className="chart-title">总吞吐量趋势 (最近10分钟)</h3>
      </div>
      <div className="chart-container">
        <Line data={chartData} options={options} />
      </div>
    </div>
  )
}

export default TotalThroughputChart