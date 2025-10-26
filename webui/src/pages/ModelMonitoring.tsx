import React, { useState, useEffect } from 'react'
import { apiService } from '../utils/api'
import { OpenAIModel, ModelInfo } from '../types/api'
import ModelListCard from '../components/ModelListCard'
import TimeRangeSelector from '../components/TimeRangeSelector'
import OverviewContent from '../components/OverviewContent'
import ModelDetailContent from '../components/ModelDetailContent'
import '../styles/ModelMonitoring.css'

const ModelMonitoring: React.FC = () => {
  const [models, setModels] = useState<OpenAIModel[]>([])
  const [selectedModel, setSelectedModel] = useState<string>('总览')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [currentTimeRange, setCurrentTimeRange] = useState<{start: Date, end: Date} | null>(null)

  useEffect(() => {
    fetchModels()
  }, [])

  const fetchModels = async () => {
    try {
      setLoading(true)
      const data = await apiService.getModels()
      setModels(data.data)
      setError(null)
    } catch (err) {
      console.error('Failed to fetch models:', err)
      setError('获取模型列表失败')
    } finally {
      setLoading(false)
    }
  }

  const handleModelSelect = (modelName: string) => {
    setSelectedModel(modelName)
  }

  const handleTimeRangeChange = (startTime: Date, endTime: Date) => {
    console.log('Time range changed:', { startTime, endTime, selectedModel })
    setCurrentTimeRange({ start: startTime, end: endTime })
    // 这里后续会用于更新右侧主页面数据
  }

  return (
    <div className="model-monitoring-page">
      <div className="model-monitoring-layout">
        {/* 左侧模型列表 */}
        <div className="model-list-section">
          <ModelListCard
            models={models}
            selectedModel={selectedModel}
            onModelSelect={handleModelSelect}
            loading={loading}
            error={error}
          />
        </div>

        {/* 右侧内容区域 */}
        <div className="model-content-section">
          {/* 时间区间选择器 */}
          <div className="time-range-section">
            <TimeRangeSelector onTimeRangeChange={handleTimeRangeChange} />
          </div>

          {/* 主页面内容区域 */}
          <div className="main-content-area">
            {selectedModel === '总览' ? (
              <OverviewContent timeRange={currentTimeRange} />
            ) : (
              <ModelDetailContent
                selectedModel={selectedModel}
                timeRange={currentTimeRange}
              />
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

export default ModelMonitoring