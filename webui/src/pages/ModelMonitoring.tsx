import React, { useState, useEffect } from 'react'
import { apiService } from '../utils/api'
import { Model, ModelInfo } from '../types/api'
import ModelListCard from '../components/ModelListCard'
import TimeRangeSelector from '../components/TimeRangeSelector'
import '../styles/ModelMonitoring.css'

const ModelMonitoring: React.FC = () => {
  const [models, setModels] = useState<Model[]>([])
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

          {/* 主页面预留区域 */}
          <div className="main-content-area">
            {/* 这里后续实现主页面内容 */}
            <div className="content-placeholder">
              <div className="content-info">
                <h3>数据监控页面</h3>
                <div className="info-grid">
                  <div className="info-item">
                    <span className="info-label">当前选中模型:</span>
                    <span className="info-value">{selectedModel}</span>
                  </div>
                  {currentTimeRange && (
                    <>
                      <div className="info-item">
                        <span className="info-label">时间区间:</span>
                        <span className="info-value">
                          {currentTimeRange.start.toLocaleString('zh-CN')} 至 {currentTimeRange.end.toLocaleString('zh-CN')}
                        </span>
                      </div>
                      <div className="info-item">
                        <span className="info-label">开始时间戳:</span>
                        <span className="info-value">{currentTimeRange.start.getTime() / 1000}</span>
                      </div>
                      <div className="info-item">
                        <span className="info-label">结束时间戳:</span>
                        <span className="info-value">{currentTimeRange.end.getTime() / 1000}</span>
                      </div>
                      <div className="info-item">
                        <span className="info-label">时间跨度:</span>
                        <span className="info-value">
                          {Math.round((currentTimeRange.end.getTime() - currentTimeRange.start.getTime()) / (1000 * 60 * 60))} 小时
                        </span>
                      </div>
                    </>
                  )}
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

export default ModelMonitoring